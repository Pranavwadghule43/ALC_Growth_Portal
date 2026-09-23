export type Role = 'ADMIN' | 'DCU' | 'SBU' | 'ALC'
export type Status = 'DRAFT' | 'SUBMITTED' | 'UNDER_REVIEW' | 'CORRECTION_REQUIRED' | 'RESUBMITTED' | 'VERIFIED' | 'REJECTED'
export interface Dcu { id: string; code: string; name: string; rcu_id: string; is_active: boolean }
export interface Sbu { id: string; code: string; name: string; is_active: boolean; dcu_id?: string }
export interface Alc { id: string; alc_code: string; alc_name: string; status: string; sbu_id?: string; sbu?: Sbu }
export interface User { id: string; username: string; email?: string; role: Role; alc_id?: string; sbu_id?: string; dcu_id?: string; alc?: Alc; sbu?: Sbu; dcu?: Dcu; is_active: boolean; must_change_password: boolean }
export interface Evidence { id: string; original_filename: string; mime_type: string; file_size: number; uploaded_at: string }
export interface Review { id: string; previous_status: Status; new_status: Status; action: string; remark?: string; reviewed_at: string }
export interface Revision { id: string; revision_number: number; change_summary: string; snapshot: Record<string, unknown>; created_at: string }
export interface Partner { id: string; alc_id: string; partner_name: string; partner_type: string; ecosystem: string; contact_person?: string; phone?: string; email?: string; location?: string; status: string; notes?: string }
export interface Activity { id: string; activity_number: string; alc_id: string; partner_id?: string; partner?: Partner; activity_type: string; ecosystem: string; collaboration_type?: string; activity_date: string; location: string; learners_reached: number; leads_generated: number; admissions_generated: number; description: string; outcome: string; status: Status; submitted_at?: string; verified_at?: string; evidence: Evidence[]; reviews: Review[]; revisions?: Revision[]; created_at: string; updated_at: string } 
export interface Task { id: string; title: string; description?: string; due_date: string; status: 'OPEN' | 'COMPLETED'; partner_id?: string }
export interface Page<T> { items: T[]; page: number; page_size: number; total: number; pages: number }

