import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, FileText, Image, Trash2, Upload, XCircle } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Evidence, Partner, Status } from '../types'
import { Badge, ErrorState, FieldError, formatDate, Loading, PageHeader } from '../components/ui'

// Local calendar date (YYYY-MM-DD). toISOString() gives the UTC date, which is "yesterday" in IST before 05:30.
function localToday() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }

const schema = z.object({
  activity_type:z.string().min(2), partner_id:z.string().optional(), ecosystem:z.string().min(2), collaboration_type:z.string().optional(),
  activity_date:z.string().min(1), location:z.string().min(2), learners_reached:z.coerce.number().int().min(0), leads_generated:z.coerce.number().int().min(0), admissions_generated:z.coerce.number().int().min(0),
  description:z.string().min(10,'Provide at least 10 characters'), outcome:z.string().min(2)
}).refine(v=>v.activity_date<=localToday(),{path:['activity_date'],message:'Future activity dates are not allowed'})
type FormValues=z.infer<typeof schema>
const activityTypes=['Prospect outreach','Partner meeting','Pilot programme','Collaboration event','Career awareness session','Admission campaign','Community engagement','Training programme']
const ecosystems=['School','College','Government','Business','Industry','NGO','Community','Training institution','Youth organization','Other']

const LOCKED_TEXT: Partial<Record<Status, string>> = {
  SUBMITTED: 'Waiting for your reviewer to review. Editing is locked until they respond.',
  RESUBMITTED: 'Resubmitted. Waiting for your reviewer to review your changes. Editing is locked until they respond.',
  UNDER_REVIEW: 'Your reviewer is reviewing this activity. Editing is locked until they respond.',
  VERIFIED: 'Verified by your reviewer. This record is final.',
  REJECTED: 'Rejected by your reviewer. This record is closed and cannot be edited or resubmitted.',
}
const ACTION_LABEL: Record<string, string> = { VERIFY: 'Verified', REQUEST_CORRECTION: 'Correction requested', REJECT: 'Rejected' }
// Who made a review decision (SBU, DCU or Admin can review since the hierarchy upgrade).
const REVIEWER: Record<string, string> = { SBU: 'your SBU', DCU: 'your DCU', ADMIN: 'Admin' }
const reviewer = (role?: string | null) => (role && REVIEWER[role]) || 'your reviewer'
const status = (s: string) => s.replaceAll('_', ' ').toLowerCase()

type TimelineItem = { key: string; date: string; title: string; detail?: string; remark?: string; tone: 'alc' | 'ok' | 'warn' | 'bad' }

function buildTimeline(activity: Activity): TimelineItem[] {
  const items: TimelineItem[] = [
    ...(activity.revisions ?? []).map(r => ({ key: `rev-${r.id}`, date: r.created_at, title: r.revision_number === 1 ? 'Submitted by your centre' : `Resubmitted by your centre (revision ${r.revision_number})`, detail: r.change_summary, tone: 'alc' as const })),
    ...activity.reviews.map(r => ({ key: `rev-${r.id}`, date: r.reviewed_at, title: `${ACTION_LABEL[r.action] ?? status(r.action)} by ${reviewer(r.reviewer_role)}${r.is_decision_change ? ' (decision changed)' : ''}`, detail: `Status: ${status(r.previous_status)} → ${status(r.new_status)}`, remark: r.remark, tone: (r.action === 'VERIFY' ? 'ok' : r.action === 'REJECT' ? 'bad' : 'warn') as TimelineItem['tone'] })),
  ]
  return items.sort((a, b) => a.date.localeCompare(b.date))
}
const TONE = { alc: 'border-slate-300', ok: 'border-emerald-500', warn: 'border-amber-500', bad: 'border-red-500' }

export default function ActivityEditor(){
  const {id}=useParams(); const navigate=useNavigate(); const location=useLocation(); const client=useQueryClient()
  const [draftId,setDraftId]=useState<string>(); const isNew=!id&&!draftId; const [files,setFiles]=useState<File[]>([]); const [progress,setProgress]=useState<Record<string,number>>({}); const [uploadErrors,setUploadErrors]=useState<Record<string,string>>({})
  // A message can arrive through navigation state (e.g. after the first save moves from /new to /:id).
  const [message,setMessage]=useState<string>(()=>(location.state as {message?:string}|null)?.message??''); const [formError,setFormError]=useState(''); const [busy,setBusy]=useState(false)
  const activityQuery=useQuery({queryKey:['activity',id],queryFn:()=>api.get<Activity>(`/portal/activities/${id}`),enabled:!!id})
  const partnersQuery=useQuery({queryKey:['partners'],queryFn:()=>api.get<Partner[]>('/portal/partners')})
  const {register,handleSubmit,reset,formState:{errors}}=useForm<FormValues>({resolver:zodResolver(schema),defaultValues:{learners_reached:0,leads_generated:0,admissions_generated:0,activity_date:localToday()}})
  useEffect(()=>{if(activityQuery.data){const a=activityQuery.data;reset({activity_type:a.activity_type,partner_id:a.partner_id??'',ecosystem:a.ecosystem,collaboration_type:a.collaboration_type??'',activity_date:a.activity_date,location:a.location,learners_reached:a.learners_reached,leads_generated:a.leads_generated,admissions_generated:a.admissions_generated,description:a.description,outcome:a.outcome})}},[activityQuery.data,reset])
  const activity=activityQuery.data; const editable=!id||activity?.status==='DRAFT'||activity?.status==='CORRECTION_REQUIRED'
  const isCorrection=activity?.status==='CORRECTION_REQUIRED'
  const latestCorrection=activity?[...activity.reviews].reverse().find(r=>r.action==='REQUEST_CORRECTION'):undefined
  const latestReject=activity?[...activity.reviews].reverse().find(r=>r.action==='REJECT'):undefined

  async function refreshAll(activityId:string){
    await Promise.all([
      client.invalidateQueries({queryKey:['activities']}),
      client.invalidateQueries({queryKey:['activity',activityId]}),
      client.invalidateQueries({queryKey:['alc-dashboard']}),
    ])
  }

  async function save(values:FormValues,submit=false){
    setBusy(true);setFormError('');setMessage('');setUploadErrors({})
    try{
      const payload={...values,partner_id:values.partner_id||null,collaboration_type:values.collaboration_type||null}
      let saved=isNew?await api.post<Activity>('/portal/activities',payload):await api.patch<Activity>(`/portal/activities/${id??draftId}`,payload)
      if(isNew)setDraftId(saved.id)
      const failed:File[]=[]
      for(const file of files){
        try{await api.uploadEvidence(`/portal/activities/${saved.id}/evidence`,file,pct=>setProgress(current=>({...current,[file.name]:pct})))}
        catch(e){failed.push(file);setUploadErrors(current=>({...current,[file.name]:e instanceof Error?e.message:'Upload failed'}))}
      }
      setFiles(failed)
      if(!failed.length)setProgress({})
      await refreshAll(saved.id)
      if(failed.length){setFormError(`Your changes were saved, but ${failed.length} evidence upload${failed.length>1?'s':''} failed. Retry the remaining files before ${isCorrection?'resubmitting':'submitting'}.`);return}
      let done:string
      if(submit){
        saved=await api.post<Activity>(`/portal/activities/${saved.id}/submit`)
        done=isCorrection?'Activity resubmitted. Your reviewer will review your changes.':'Activity submitted for verification.'
        await refreshAll(saved.id)
      } else done=isCorrection?'Changes saved. Resubmit when you are ready.':'Draft saved successfully.'
      if(!id)navigate(`/portal/activities/${saved.id}`,{replace:true,state:{message:done}})
      else setMessage(done)
    }catch(e){setFormError(e instanceof Error?e.message:'Unable to save activity')}
    finally{setBusy(false)}
  }
  async function removeEvidence(e:Evidence){
    const warning=isCorrection?`Remove ${e.original_filename} from this activity? The copy your reviewer already saw is kept in the review record.`:`Remove ${e.original_filename}?`
    if(!confirm(warning))return
    setFormError('');setMessage('')
    try{await api.delete(`/portal/evidence/${e.id}`);await client.invalidateQueries({queryKey:['activity',id]});setMessage(`${e.original_filename} removed.`)}
    catch(err){setFormError(err instanceof Error?err.message:'Unable to remove evidence')}
  }
  async function openEvidence(e:Evidence){
    setFormError('')
    try{const {url}=await api.get<{url:string}>(`/portal/evidence/${e.id}/access`);window.open(url,'_blank','noopener,noreferrer')}
    catch(err){setFormError(err instanceof Error?err.message:'Unable to open evidence')}
  }
  function confirmSubmit(v:FormValues){
    const text=isCorrection?'Resubmit this activity? It goes back to your reviewer for review and stays locked until they respond.':'Submit this activity? Details and evidence will be locked until review.'
    if(confirm(text))save(v,true)
  }
  if(id&&activityQuery.isLoading)return <Loading/>;if(activityQuery.error)return <ErrorState error={activityQuery.error}/>
  const timeline=activity?buildTimeline(activity):[]
  const description=isNew?'Complete the details, attach evidence, then save or submit.':isCorrection?'Your reviewer asked for changes. Update the details or evidence, then resubmit.':editable?'Complete the details, attach evidence, then save or submit.':(activity&&LOCKED_TEXT[activity.status])??'This activity is locked in its current workflow status.'
  return <><PageHeader title={isNew?'Record an activity':activity?.activity_number??'Activity'} description={description} actions={activity&&<Badge status={activity.status}/>}/>
    {isCorrection&&<div className="mb-5 flex gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700"/><div className="min-w-0"><p className="font-semibold text-amber-900">Correction requested by {reviewer(latestCorrection?.reviewer_role)}{latestCorrection&&<span className="font-normal text-amber-800"> · {formatDate(latestCorrection.reviewed_at)}</span>}</p><p className="mt-1 whitespace-pre-line text-sm text-amber-900">{latestCorrection?.remark||'No remark was given.'}</p><p className="mt-2 text-xs text-amber-800">Next step: make the changes below, then click <b>Resubmit for verification</b>.</p></div></div>}
    {activity?.status==='REJECTED'&&<div className="mb-5 flex gap-3 rounded-lg border border-red-300 bg-red-50 p-4"><XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-700"/><div className="min-w-0"><p className="font-semibold text-red-900">Rejected by {reviewer(latestReject?.reviewer_role)}{latestReject&&<span className="font-normal text-red-800"> · {formatDate(latestReject.reviewed_at)}</span>}</p><p className="mt-1 whitespace-pre-line text-sm text-red-900">{latestReject?.remark||'No reason was given.'}</p><p className="mt-2 text-xs text-red-800">This record is closed. If the work was valid, <Link to="/portal/activities/new" className="font-semibold underline">record a new activity</Link> with the correct details.</p></div></div>}
    <form onSubmit={handleSubmit(v=>save(v,false))} className="space-y-6"><section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Activity details</h2><fieldset disabled={!editable||busy} className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"><div><label>Activity Type</label><select className="mt-1.5" {...register('activity_type')}><option value="">Select type</option>{activityTypes.map(x=><option key={x}>{x}</option>)}</select><FieldError message={errors.activity_type?.message}/></div><div><label>Partner / Institution</label><select className="mt-1.5" {...register('partner_id')}><option value="">No linked partner</option>{partnersQuery.data?.map(p=><option key={p.id} value={p.id}>{p.partner_name}</option>)}</select></div><div><label>Ecosystem</label><select className="mt-1.5" {...register('ecosystem')}><option value="">Select ecosystem</option>{ecosystems.map(x=><option key={x}>{x}</option>)}</select><FieldError message={errors.ecosystem?.message}/></div><div><label>Collaboration Type</label><input className="mt-1.5" placeholder="e.g. MoU, event, referral" {...register('collaboration_type')}/></div><div><label>Activity Date</label><input className="mt-1.5" type="date" max={localToday()} {...register('activity_date')}/><FieldError message={errors.activity_date?.message}/></div><div><label>Location</label><input className="mt-1.5" {...register('location')}/><FieldError message={errors.location?.message}/></div></fieldset></section>
      <section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Reach and outcomes</h2><fieldset disabled={!editable||busy}><div className="grid gap-4 sm:grid-cols-3"><div><label>Learners Reached</label><input className="mt-1.5" type="number" min="0" {...register('learners_reached')}/></div><div><label>Leads Generated</label><input className="mt-1.5" type="number" min="0" {...register('leads_generated')}/></div><div><label>Admissions Generated</label><input className="mt-1.5" type="number" min="0" {...register('admissions_generated')}/></div></div><div className="mt-4 grid gap-4 md:grid-cols-2"><div><label>Description</label><textarea className="mt-1.5 min-h-32" {...register('description')}/><FieldError message={errors.description?.message}/></div><div><label>Outcome</label><textarea className="mt-1.5 min-h-32" {...register('outcome')}/><FieldError message={errors.outcome?.message}/></div></div></fieldset></section>
      <section className="panel p-5"><h2 className="font-bold text-navy">Evidence</h2><p className="mt-1 text-sm text-slate-500">JPG, PNG, WEBP, or PDF · up to 10 MB each · maximum 10 files</p>{editable&&<label className="mt-4 flex cursor-pointer flex-col items-center rounded-lg border-2 border-dashed p-6 text-center hover:border-teal hover:bg-teal/5"><Upload className="mb-2 text-teal"/><span className="text-sm font-semibold">Choose photos or PDFs</span><span className="mt-1 text-xs text-slate-500">New files are uploaded when you save{isCorrection?' or resubmit':' or submit'}.</span><input className="hidden" type="file" accept="image/jpeg,image/png,image/webp,application/pdf" multiple onChange={e=>setFiles(Array.from(e.target.files??[]))}/></label>} {files.length>0&&<div className="mt-4 space-y-2">{files.map((f,i)=><div key={`${f.name}-${i}`} className="flex items-center justify-between rounded-md bg-slate-50 p-3 text-sm"><span>{f.name} · {(f.size/1024/1024).toFixed(1)} MB</span><button type="button" title="Remove from upload list" onClick={()=>setFiles(x=>x.filter((_,j)=>i!==j))}><Trash2 className="h-4 w-4 text-red-700"/></button></div>)}</div>}
        {Object.entries(progress).map(([name,percent])=><div key={name} className="mt-2 text-xs text-slate-600">{name}: {percent}%<div className="mt-1 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-teal" style={{width:`${percent}%`}}/></div>{uploadErrors[name]&&<p className="mt-1 text-red-700">{uploadErrors[name]}</p>}</div>)}
        {activity&&!activity.evidence.length&&!files.length&&<p className="mt-4 text-sm text-slate-500">No evidence attached yet.</p>}
        {!!activity?.evidence.length&&<div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{activity.evidence.map(e=><div key={e.id} className="flex items-center gap-3 rounded-md border p-3">{e.mime_type==='application/pdf'?<FileText className="text-red-700"/>:<Image className="text-teal"/>}<button type="button" onClick={()=>openEvidence(e)} title={e.original_filename} className="min-w-0 flex-1 truncate text-left text-sm font-semibold hover:text-teal">{e.original_filename}</button>{editable&&<button type="button" title="Delete this file" onClick={()=>removeEvidence(e)}><Trash2 className="h-4 w-4 text-red-700"/></button>}</div>)}</div>}</section>
      {timeline.length>0&&<section className="panel p-5"><h2 className="mb-1 font-bold text-navy">Submission and review history</h2><p className="mb-4 text-sm text-slate-500">Every submission by your centre and every review decision, oldest first.</p><ol className="space-y-4">{timeline.map(t=><li key={t.key} className={`border-l-2 pl-4 ${TONE[t.tone]}`}><div className="flex flex-wrap items-center gap-2"><b className="text-sm">{t.title}</b><span className="text-xs text-slate-500">{formatDate(t.date)}</span></div>{t.detail&&<p className="mt-0.5 text-xs text-slate-500">{t.detail}</p>}{t.remark&&<p className="mt-1 whitespace-pre-line text-sm text-slate-700">“{t.remark}”</p>}</li>)}</ol></section>}
      {formError&&<p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{formError}</p>}{message&&<p role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</p>}
      {editable&&<div className="flex flex-wrap items-center justify-end gap-3">{isCorrection&&<span className="mr-auto text-xs text-slate-500">Saving keeps your changes without sending them. Resubmit sends them to your reviewer.</span>}<button type="submit" className="btn-secondary" disabled={busy}>{busy?'Saving…':isCorrection?'Save changes':'Save draft'}</button><button type="button" className="btn-primary" disabled={busy} onClick={handleSubmit(confirmSubmit)}>{busy?'Please wait…':isCorrection?'Resubmit for verification':'Submit for verification'}</button></div>}
    </form></>
}