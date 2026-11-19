<script lang="ts">
  /**
   * One import run. While it is queued or running the page asks again every
   * few seconds; nobody has to stay, since the report can be emailed. A
   * finished preview offers to import the same files.
   */
  import { enhance } from '$app/forms';
  import { invalidateAll } from '$app/navigation';
  import { resolve } from '$app/paths';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import { relativeTime } from '#lib/v2/format.js';
  import { ChevronRight } from '@lucide/svelte';
  import ImportReport from '../ImportReport.svelte';
  import { RUN_STATUS } from '../run-status.js';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let run = $derived(data.run);
  let waiting = $derived(run.status === 'QUEUED' || run.status === 'RUNNING');

  $effect(() => {
    if (!waiting) return;
    const timer = setInterval(() => invalidateAll(), 3000);
    return () => clearInterval(timer);
  });
</script>

<PageHeader title="{run.source} {run.dry_run ? 'preview' : 'import'}">
  {#snippet crumb()}
    <a href={resolve('invoices/catalog/import')}>Import</a>
    <ChevronRight size={12} />
  {/snippet}
  {#snippet sub()}
    Started {relativeTime(run.created_at)} by {run.started_by}
    {#if run.files.length}· {run.files.join(', ')}{/if}
  {/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <section class="v2-card status" aria-label="Status">
      <header>
        <Pill tone={RUN_STATUS[run.status].tone}>{RUN_STATUS[run.status].label}</Pill>
        {#if run.dry_run && run.status === 'DONE'}
          <Pill tone="slate">Nothing was saved</Pill>
        {:else if !run.dry_run && run.status === 'DONE'}
          <Pill tone="moss">Saved</Pill>
        {/if}
      </header>
      {#if waiting}
        <p class="v2-sub" role="status">
          {run.status === 'QUEUED' ? 'Waiting to start.' : 'Reading the report.'} This page updates by
          itself, and you can leave it.
          {#if run.notify}The report will be emailed to you when it finishes.{:else}A run that takes
            longer than a minute is emailed to you.{/if}
        </p>
      {:else if run.status === 'FAILED'}
        <p class="error" role="alert">{run.error}</p>
      {/if}
      {#if run.emailed_at}
        <p class="v2-sub">
          The report was emailed to {run.started_by}
          {relativeTime(run.emailed_at)}.
        </p>
      {/if}
      {#if run.files_kept}
        <form method="POST" action="?/import" use:enhance class="again">
          <label class="notify">
            <input type="checkbox" name="notify" />
            Email me the report when it finishes
          </label>
          <button class="v2-btn v2-btn-primary" type="submit">Import these files</button>
        </form>
      {/if}
      {#if form?.error}
        <p class="error" role="alert">{form.error}</p>
      {/if}
    </section>

    {#if run.report}
      <ImportReport report={run.report} />
    {/if}
  </div>
</div>

<style>
  .status {
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  p {
    font-size: 12.5px;
    margin: 6px 0 0;
    max-width: 72ch;
  }
  .again {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 12px;
  }
  .notify {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
  }
  .error {
    color: var(--v2-rust);
  }
  @media (max-width: 768px) {
    .v2-btn {
      min-height: 44px;
      flex: 1 1 100%;
    }
  }
</style>
