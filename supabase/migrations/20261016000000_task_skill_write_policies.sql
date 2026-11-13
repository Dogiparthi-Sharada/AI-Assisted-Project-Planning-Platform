-- A FOR ALL policy with read access in USING also allowed readers to DELETE
-- when their database role had DELETE privileges. Separate policies ensure
-- only project writers can change task skill requirements.
drop policy "task_skill_requirements: via wbs node" on public.task_skill_requirements;

create policy "task_skill_requirements: project readers can read"
  on public.task_skill_requirements for select to authenticated
  using (exists (
    select 1 from public.wbs_nodes n
    where n.id = task_skill_requirements.wbs_node_id
      and (select private.can_read_project(n.project_id))
  ));

create policy "task_skill_requirements: project writers can insert"
  on public.task_skill_requirements for insert to authenticated
  with check (exists (
    select 1 from public.wbs_nodes n
    where n.id = task_skill_requirements.wbs_node_id
      and (select private.can_write_project(n.project_id))
  ));

create policy "task_skill_requirements: project writers can update"
  on public.task_skill_requirements for update to authenticated
  using (exists (
    select 1 from public.wbs_nodes n
    where n.id = task_skill_requirements.wbs_node_id
      and (select private.can_write_project(n.project_id))
  ))
  with check (exists (
    select 1 from public.wbs_nodes n
    where n.id = task_skill_requirements.wbs_node_id
      and (select private.can_write_project(n.project_id))
  ));

create policy "task_skill_requirements: project writers can delete"
  on public.task_skill_requirements for delete to authenticated
  using (exists (
    select 1 from public.wbs_nodes n
    where n.id = task_skill_requirements.wbs_node_id
      and (select private.can_write_project(n.project_id))
  ));
