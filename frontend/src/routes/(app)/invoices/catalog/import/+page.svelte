<script lang="ts">
  /**
   * Import a channel's report. Mercado Libre takes files; every other channel
   * is listed with its upload disabled until its report has been analyzed and
   * an importer written for it. A run goes to the background and opens its
   * own page; Preview runs the whole import and saves nothing.
   */
  import { applyAction, deserialize } from '$app/forms';
  import { resolve } from '$app/paths';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import { relativeTime } from '#lib/v2/format.js';
  import { RUN_STATUS } from './run-status.js';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let working = $state('');
  let refused = $state<{ source: string; error: string } | null>(null);
  let failure = $derived(
    refused ?? (form && 'error' in form ? { source: form.source, error: form.error } : null)
  );

  /**
   * Posted by hand, as the other importers do: the Node server refuses a body
   * over its BODY_SIZE_LIMIT (the image sets 45M) with a plain-text 413 before
   * the action runs, which the form helpers cannot read.
   */
  async function send(event: SubmitEvent & { currentTarget: HTMLFormElement }) {
    event.preventDefault();
    if (working) return;
    const formEl = event.currentTarget;
    const source = (new FormData(formEl).get('source') ?? '').toString();
    working = source;
    refused = null;
    try {
      const response = await fetch(formEl.action, {
        method: 'POST',
        body: new FormData(formEl, event.submitter)
      });
      if (response.status === 413) {
        refused = {
          source,
          error:
            'The files together are larger than this server accepts. Ask whoever runs it to raise BODY_SIZE_LIMIT.'
        };
        return;
      }
      await applyAction(deserialize(await response.text()));
    } catch {
      refused = { source, error: 'The files could not be sent. Try again.' };
    } finally {
      working = '';
    }
  }
</script>

<PageHeader title="Import">
  {#snippet sub()}A channel's report, into the catalog{/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if !data.can_manage}
      <p class="v2-sub">Only an administrator can import into the catalog.</p>
    {:else}
      {#each data.sources as s (s.code)}
        <section class="v2-card source" aria-label="Import from {s.name}">
          <header>
            <h2>{s.name}</h2>
            {#if s.available}
              <Pill tone="moss">Available</Pill>
            {:else}
              <Pill tone="slate">Not available yet</Pill>
            {/if}
          </header>
          {#if s.available}
            <form method="POST" action="?/start" enctype="multipart/form-data" onsubmit={send}>
              <input type="hidden" name="source" value={s.code} />
              {#each s.files as f (f.field)}
                <label class="file">
                  <span>{f.label}</span>
                  <input
                    class="v2-input"
                    type="file"
                    name={f.field}
                    accept=".xlsx"
                    required
                    aria-label={f.label}
                  />
                </label>
              {/each}
              <label class="notify">
                <input type="checkbox" name="notify" />
                Email me the report when it finishes
              </label>
              <p class="v2-sub hint">
                Each file can be up to 20 MB. The import runs in the background, so you can leave
                this page; a run that takes longer than a minute is emailed to you anyway. Preview
                runs the whole import and saves nothing.
              </p>
              <div class="buttons">
                <button
                  class="v2-btn"
                  type="submit"
                  name="mode"
                  value="preview"
                  disabled={!!working}
                >
                  {working === s.code ? 'Sending...' : 'Preview'}
                </button>
                <button
                  class="v2-btn v2-btn-primary"
                  type="submit"
                  name="mode"
                  value="import"
                  disabled={!!working}
                >
                  Import
                </button>
              </div>
              {#if failure?.source === s.code}
                <p class="error" role="alert">{failure.error}</p>
              {/if}
            </form>
          {:else}
            <p class="v2-sub reason">{s.reason}</p>
            <div class="buttons">
              <input
                class="v2-input"
                type="file"
                disabled
                aria-label="Files from {s.name} (not available yet)"
              />
              <button class="v2-btn" type="button" disabled>Preview</button>
              <button class="v2-btn" type="button" disabled>Import</button>
            </div>
          {/if}
        </section>
      {/each}

      <h2 class="v2-label runs-head">Recent imports</h2>
      {#if data.runs.length === 0}
        <p class="v2-sub">Nothing imported yet.</p>
      {:else}
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Started</th>
                <th>From</th>
                <th>What</th>
                <th>Status</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {#each data.runs as run (run.id)}
                <tr>
                  <td data-m="title">
                    <a class="v2-row-link" href={resolve(`invoices/catalog/import/${run.id}`)}>
                      <span class="v2-table-primary">{relativeTime(run.created_at)}</span>
                    </a>
                  </td>
                  <td data-m="meta">{run.source}</td>
                  <td data-m="meta">{run.dry_run ? 'Preview' : 'Import'}</td>
                  <td data-m="tag">
                    <Pill tone={RUN_STATUS[run.status].tone}>{RUN_STATUS[run.status].label}</Pill>
                  </td>
                  <td data-m="meta">{run.started_by}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .source {
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  h2 {
    font-size: 14px;
    margin: 0;
  }
  .file {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;
    font-size: 12.5px;
  }
  .notify {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
  }
  .hint,
  .reason {
    font-size: 12.5px;
    max-width: 72ch;
    margin: 8px 0 12px;
  }
  .buttons {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  .buttons .v2-input {
    flex: 1 1 16rem;
    min-width: 0;
  }
  .runs-head {
    margin: 22px 0 10px;
  }
  .error {
    color: var(--v2-rust);
    font-size: 12.5px;
    margin: 10px 0 0;
  }
  @media (max-width: 768px) {
    .v2-btn,
    .v2-input {
      min-height: 44px;
    }
    .buttons .v2-btn {
      flex: 1 1 100%;
    }
  }
</style>
