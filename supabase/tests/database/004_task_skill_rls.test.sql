-- Exercise RLS with explicit transaction-local grants, independent of the
-- target project's Supabase default-privilege settings. Nothing persists.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;
select plan(7);

grant select on public.wbs_nodes to authenticated;
grant select, insert, update, delete on public.task_skill_requirements to authenticated;

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('b0000000-0000-0000-0000-000000000001', 'skill-owner@example.com', '{}', 'authenticated', 'authenticated'),
  ('b0000000-0000-0000-0000-000000000002', 'skill-viewer@example.com', '{}', 'authenticated', 'authenticated');

create temp table skill_test_org as
select org_id from public.organization_members
where user_id = 'b0000000-0000-0000-0000-000000000001' and role = 'owner';
insert into public.organization_members (org_id, user_id, role)
select org_id, 'b0000000-0000-0000-0000-000000000002', 'viewer' from skill_test_org;
insert into public.projects (id, org_id, name)
select 'b1000000-0000-0000-0000-000000000001', org_id, 'Skill access test' from skill_test_org;
insert into public.wbs_nodes (id, project_id, level, name)
values ('b2000000-0000-0000-0000-000000000001', 'b1000000-0000-0000-0000-000000000001', 1, 'Root');
insert into public.skills (id, org_id, name)
select 'b3000000-0000-0000-0000-000000000001', org_id, 'Testing' from skill_test_org;
insert into public.skills (id, org_id, name)
select 'b3000000-0000-0000-0000-000000000002', org_id, 'Other skill' from skill_test_org;
insert into public.task_skill_requirements (wbs_node_id, skill_id, min_proficiency)
values ('b2000000-0000-0000-0000-000000000001', 'b3000000-0000-0000-0000-000000000001', 1);

set local role authenticated;
set local request.jwt.claims = '{"sub":"b0000000-0000-0000-0000-000000000002","role":"authenticated"}';
select is((select count(*)::int from public.task_skill_requirements), 1,
  'viewer can select task skill requirements for a readable project');
select throws_ok(
  $$ insert into public.task_skill_requirements (wbs_node_id, skill_id, min_proficiency)
     values ('b2000000-0000-0000-0000-000000000001', 'b3000000-0000-0000-0000-000000000002', 1) $$,
  '42501', null, 'viewer cannot insert a task skill requirement');
update public.task_skill_requirements set min_proficiency = 5
where wbs_node_id = 'b2000000-0000-0000-0000-000000000001';
select is((select min_proficiency::int from public.task_skill_requirements
  where wbs_node_id = 'b2000000-0000-0000-0000-000000000001'), 1,
  'viewer cannot update a task skill requirement');
with deleted as (
  delete from public.task_skill_requirements
  where wbs_node_id = 'b2000000-0000-0000-0000-000000000001' returning 1
)
select is((select count(*)::int from deleted), 0,
  'viewer cannot delete a task skill requirement even with DELETE grant');

set local request.jwt.claims = '{"sub":"b0000000-0000-0000-0000-000000000001","role":"authenticated"}';
update public.task_skill_requirements set min_proficiency = 3
where wbs_node_id = 'b2000000-0000-0000-0000-000000000001';
select is((select min_proficiency::int from public.task_skill_requirements
  where wbs_node_id = 'b2000000-0000-0000-0000-000000000001'), 3,
  'project writer can update a task skill requirement');
with deleted as (
  delete from public.task_skill_requirements
  where wbs_node_id = 'b2000000-0000-0000-0000-000000000001' returning 1
)
select is((select count(*)::int from deleted), 1,
  'project writer can delete a task skill requirement');
select lives_ok(
  $$ insert into public.task_skill_requirements (wbs_node_id, skill_id, min_proficiency)
     values ('b2000000-0000-0000-0000-000000000001', 'b3000000-0000-0000-0000-000000000002', 2) $$,
  'project writer can insert a task skill requirement');

select * from finish();
rollback;
