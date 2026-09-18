import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { FileText, Image, Trash2, Upload } from 'lucide-react'
import { api } from '../lib/api'
import type { Activity, Evidence, Partner } from '../types'
import { Badge, ErrorState, FieldError, formatDate, Loading, PageHeader } from '../components/ui'

const schema = z.object({
  activity_type:z.string().min(2), partner_id:z.string().optional(), ecosystem:z.string().min(2), collaboration_type:z.string().optional(),
  activity_date:z.string().min(1), location:z.string().min(2), learners_reached:z.coerce.number().int().min(0), leads_generated:z.coerce.number().int().min(0), admissions_generated:z.coerce.number().int().min(0),
  description:z.string().min(10,'Provide at least 10 characters'), outcome:z.string().min(2)
}).refine(v=>v.activity_date<=new Date().toISOString().slice(0,10),{path:['activity_date'],message:'Future activity dates are not allowed'})
type FormValues=z.infer<typeof schema>
const activityTypes=['Prospect outreach','Partner meeting','Pilot programme','Collaboration event','Career awareness session','Admission campaign','Community engagement','Training programme']
const ecosystems=['School','College','Government','Business','Industry','NGO','Community','Training institution','Youth organization','Other']

export default function ActivityEditor(){
  const {id}=useParams(); const navigate=useNavigate(); const client=useQueryClient(); const [draftId,setDraftId]=useState<string>(); const isNew=!id&&!draftId; const [files,setFiles]=useState<File[]>([]); const [progress,setProgress]=useState<Record<string,number>>({}); const [uploadErrors,setUploadErrors]=useState<Record<string,string>>({}); const [message,setMessage]=useState(''); const [formError,setFormError]=useState(''); const [busy,setBusy]=useState(false)
  const activityQuery=useQuery({queryKey:['activity',id],queryFn:()=>api.get<Activity>(`/alc/activities/${id}`),enabled:!!id})
  const partnersQuery=useQuery({queryKey:['partners'],queryFn:()=>api.get<Partner[]>('/alc/partners')})
  const {register,handleSubmit,reset,formState:{errors}}=useForm<FormValues>({resolver:zodResolver(schema),defaultValues:{learners_reached:0,leads_generated:0,admissions_generated:0,activity_date:new Date().toISOString().slice(0,10)}})
  useEffect(()=>{if(activityQuery.data){const a=activityQuery.data;reset({activity_type:a.activity_type,partner_id:a.partner_id??'',ecosystem:a.ecosystem,collaboration_type:a.collaboration_type??'',activity_date:a.activity_date,location:a.location,learners_reached:a.learners_reached,leads_generated:a.leads_generated,admissions_generated:a.admissions_generated,description:a.description,outcome:a.outcome})}},[activityQuery.data,reset])
  const activity=activityQuery.data; const editable=!id||activity?.status==='DRAFT'||activity?.status==='CORRECTION_REQUIRED'
  async function save(values:FormValues,submit=false){
    setBusy(true);setFormError('');setMessage('');setUploadErrors({})
    try{
      const payload={...values,partner_id:values.partner_id||null,collaboration_type:values.collaboration_type||null}
      let saved=isNew?await api.post<Activity>('/alc/activities',payload):await api.patch<Activity>(`/alc/activities/${id??draftId}`,payload)
      if(isNew)setDraftId(saved.id)
      const failed:File[]=[]
      for(const file of files){
        try{await api.uploadEvidence(`/alc/activities/${saved.id}/evidence`,file,pct=>setProgress(current=>({...current,[file.name]:pct})))}
        catch(e){failed.push(file);setUploadErrors(current=>({...current,[file.name]:e instanceof Error?e.message:'Upload failed'}))}
      }
      setFiles(failed)
      if(!failed.length)setProgress({})
      await client.invalidateQueries({queryKey:['activities']})
      if(failed.length){setFormError('The draft was saved, but some evidence uploads failed. Retry the remaining files before submitting.');return}
      if(submit){saved=await api.post<Activity>(`/alc/activities/${saved.id}/submit`);setMessage('Activity submitted for verification.')}
      else setMessage('Draft saved successfully.')
      await client.invalidateQueries({queryKey:['activity',saved.id]})
      if(!id)navigate(`/alc/activities/${saved.id}`,{replace:true})
    }catch(e){setFormError(e instanceof Error?e.message:'Unable to save activity')}
    finally{setBusy(false)}
  }
  async function removeEvidence(e:Evidence){if(!confirm(`Remove ${e.original_filename}?`))return;await api.delete(`/alc/evidence/${e.id}`);client.invalidateQueries({queryKey:['activity',id]})}
  async function openEvidence(e:Evidence){const {url}=await api.get<{url:string}>(`/alc/evidence/${e.id}/access`);window.open(url,'_blank','noopener,noreferrer')}
  if(id&&activityQuery.isLoading)return <Loading/>;if(activityQuery.error)return <ErrorState error={activityQuery.error}/>
  return <><PageHeader title={isNew?'Record an activity':activity?.activity_number??'Activity'} description={editable?'Complete the details, attach evidence, then save or submit.':'This activity is locked in its current workflow status.'} actions={activity&&<Badge status={activity.status}/>}/>
    {activity?.status==='CORRECTION_REQUIRED'&&<div className="mb-5 rounded-lg border border-amber-300 bg-amber-50 p-4"><p className="font-semibold text-amber-900">Correction requested</p><p className="mt-1 text-sm text-amber-800">{[...activity.reviews].reverse().find(r=>r.action==='REQUEST_CORRECTION')?.remark}</p></div>}
    <form onSubmit={handleSubmit(v=>save(v,false))} className="space-y-6"><section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Activity details</h2><fieldset disabled={!editable||busy} className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"><div><label>Activity Type</label><select className="mt-1.5" {...register('activity_type')}><option value="">Select type</option>{activityTypes.map(x=><option key={x}>{x}</option>)}</select><FieldError message={errors.activity_type?.message}/></div><div><label>Partner / Institution</label><select className="mt-1.5" {...register('partner_id')}><option value="">No linked partner</option>{partnersQuery.data?.map(p=><option key={p.id} value={p.id}>{p.partner_name}</option>)}</select></div><div><label>Ecosystem</label><select className="mt-1.5" {...register('ecosystem')}><option value="">Select ecosystem</option>{ecosystems.map(x=><option key={x}>{x}</option>)}</select><FieldError message={errors.ecosystem?.message}/></div><div><label>Collaboration Type</label><input className="mt-1.5" placeholder="e.g. MoU, event, referral" {...register('collaboration_type')}/></div><div><label>Activity Date</label><input className="mt-1.5" type="date" max={new Date().toISOString().slice(0,10)} {...register('activity_date')}/><FieldError message={errors.activity_date?.message}/></div><div><label>Location</label><input className="mt-1.5" {...register('location')}/><FieldError message={errors.location?.message}/></div></fieldset></section>
      <section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Reach and outcomes</h2><fieldset disabled={!editable||busy}><div className="grid gap-4 sm:grid-cols-3"><div><label>Learners Reached</label><input className="mt-1.5" type="number" min="0" {...register('learners_reached')}/></div><div><label>Leads Generated</label><input className="mt-1.5" type="number" min="0" {...register('leads_generated')}/></div><div><label>Admissions Generated</label><input className="mt-1.5" type="number" min="0" {...register('admissions_generated')}/></div></div><div className="mt-4 grid gap-4 md:grid-cols-2"><div><label>Description</label><textarea className="mt-1.5 min-h-32" {...register('description')}/><FieldError message={errors.description?.message}/></div><div><label>Outcome</label><textarea className="mt-1.5 min-h-32" {...register('outcome')}/><FieldError message={errors.outcome?.message}/></div></div></fieldset></section>
      <section className="panel p-5"><h2 className="font-bold text-navy">Evidence</h2><p className="mt-1 text-sm text-slate-500">JPG, PNG, WEBP, or PDF · up to 10 MB each · maximum 10 files</p>{editable&&<label className="mt-4 flex cursor-pointer flex-col items-center rounded-lg border-2 border-dashed p-6 text-center hover:border-teal hover:bg-teal/5"><Upload className="mb-2 text-teal"/><span className="text-sm font-semibold">Choose photos or PDFs</span><span className="mt-1 text-xs text-slate-500">On mobile, you can select photos from your camera.</span><input className="hidden" type="file" accept="image/jpeg,image/png,image/webp,application/pdf" multiple capture="environment" onChange={e=>setFiles(Array.from(e.target.files??[]))}/></label>} {files.length>0&&<div className="mt-4 space-y-2">{files.map((f,i)=><div key={`${f.name}-${i}`} className="flex items-center justify-between rounded-md bg-slate-50 p-3 text-sm"><span>{f.name} · {(f.size/1024/1024).toFixed(1)} MB</span><button type="button" onClick={()=>setFiles(x=>x.filter((_,j)=>i!==j))}><Trash2 className="h-4 w-4 text-red-700"/></button></div>)}</div>}
        {Object.entries(progress).map(([name,percent])=><div key={name} className="mt-2 text-xs text-slate-600">{name}: {percent}%<div className="mt-1 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-teal" style={{width:`${percent}%`}}/></div>{uploadErrors[name]&&<p className="mt-1 text-red-700">{uploadErrors[name]}</p>}</div>)}
        {!!activity?.evidence.length&&<div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{activity.evidence.map(e=><div key={e.id} className="flex items-center gap-3 rounded-md border p-3">{e.mime_type==='application/pdf'?<FileText className="text-red-700"/>:<Image className="text-teal"/>}<button type="button" onClick={()=>openEvidence(e)} className="min-w-0 flex-1 truncate text-left text-sm font-semibold hover:text-teal">{e.original_filename}</button>{editable&&<button type="button" onClick={()=>removeEvidence(e)}><Trash2 className="h-4 w-4 text-red-700"/></button>}</div>)}</div>}</section>
      {!!activity?.reviews.length&&<section className="panel p-5"><h2 className="mb-4 font-bold text-navy">Review history</h2><div className="space-y-3">{activity.reviews.map(r=><div key={r.id} className="border-l-2 border-teal pl-4"><div className="flex flex-wrap items-center gap-2"><b className="text-sm">{r.action.replaceAll('_',' ')}</b><span className="text-xs text-slate-500">{formatDate(r.reviewed_at)}</span></div>{r.remark&&<p className="mt-1 text-sm text-slate-600">{r.remark}</p>}</div>)}</div></section>}
      {formError&&<p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{formError}</p>}{message&&<p className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</p>}{editable&&<div className="flex flex-wrap justify-end gap-3"><button type="submit" className="btn-secondary" disabled={busy}>{busy?'Saving…':'Save Draft'}</button><button type="button" className="btn-primary" disabled={busy} onClick={handleSubmit(v=>{if(confirm('Submit this activity? Details and evidence will be locked until review.'))save(v,true)})}>Submit for Verification</button></div>}
    </form></>
}
