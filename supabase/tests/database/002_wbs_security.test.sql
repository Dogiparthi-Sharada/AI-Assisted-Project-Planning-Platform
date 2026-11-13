-- WBS mutation and tenant isolation using the actual RLS policies and triggers.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;
select plan(9);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('a0000000-0000-0000-0000-000000000001', 'wbs-owner@example.com', '{}', 'authenticated', 'authenticated'),
  ('a0000000-0000-0000-0000-000000000002', 'wbs-other@example.com', '{}', 'authenticated', 'authenticated'),
  ('a0000000-0000-0000-0000-000000000003', 'wbs-viewer@example.com', '{}', 'authenticated', 'authenticated');

create temp table wbs_test_org as
select org_id from public.organization_members
where user_id = 'a0000000-0000-0000-0000-000000000001' and role = 'owner';
grant select on wbs_test_org to authenticated;
insert into public.organization_members (org_id, user_id, role)
select org_id, 'a0000000-0000-0000-0000-000000000003', 'viewer' from wbs_test_org;

set local role authenticated;
set local request.jwt.claims = '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated"}';
insert into public.projects (id, org_id, name)
select 'a1000000-0000-0000-0000-000000000001', org_id, 'WBS test' from wbs_test_org;
insert into public.projects (id, org_id, name)
select 'a1000000-0000-0000-0000-000000000002', org_id, 'Other project' from wbs_test_org;

select lives_ok(
  $$ insert into public.wbs_nodes (id, project_id, level, name)
     values ('a2000000-0000-0000-0000-000000000001', 'a1000000-0000-0000-0000-000000000001', 1, 'Root') $$,
  'project owner can create root');
select lives_ok(
  $$ insert into public.wbs_nodes (id, project_id, parent_id, level, name, duration_days)
     values ('a2000000-0000-0000-0000-000000000002', 'a1000000-0000-0000-0000-000000000001',
             'a2000000-0000-0000-0000-000000000001', 2, 'Package', 5) $$,
  'project owner can create a work package');
select throws_ok(
  $$ insert into public.wbs_nodes (project_id, parent_id, level, name)
     values ('a1000000-0000-0000-0000-000000000002', 'a2000000-0000-0000-0000-000000000001', 2, 'Cross project') $$,
  'P0001', 'WBS parent belongs to a different project', 'cross-project parents are rejected');

set local request.jwt.claims = '{"sub":"a0000000-0000-0000-0000-000000000003","role":"authenticated"}';
select is((select count(*)::int from public.wbs_nodes), 2, 'viewer can read WBS');
select throws_ok(
  $$ insert into public.wbs_nodes (project_id, parent_id, level, name)
     values ('a1000000-0000-0000-0000-000000000001', 'a2000000-0000-0000-0000-000000000001', 2, 'Viewer insert') $$,
  '42501', null, 'viewer cannot insert WBS');
update public.wbs_nodes set name = 'viewer changed' where id = 'a2000000-0000-0000-0000-000000000002';
select is((select name from public.wbs_nodes where id = 'a2000000-0000-0000-0000-000000000002'),
  'Package', 'viewer cannot update WBS');

set local request.jwt.claims = '{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}';
select is((select count(*)::int from public.wbs_nodes), 0, 'other tenant cannot read WBS');
update public.wbs_nodes set name = 'other changed' where id = 'a2000000-0000-0000-0000-000000000002';
reset role;
select is((select name from public.wbs_nodes where id = 'a2000000-0000-0000-0000-000000000002'),
  'Package', 'other tenant cannot update WBS');

set local role authenticated;
set local request.jwt.claims = '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated"}';
delete from public.wbs_nodes where id = 'a2000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.wbs_nodes), 0, 'deleting root cascades to children');
select * from finish();
rollback;
