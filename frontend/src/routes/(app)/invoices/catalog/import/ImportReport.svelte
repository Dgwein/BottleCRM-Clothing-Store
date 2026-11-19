<script lang="ts">
  /** An import's report: what was created and updated, the categories left
   * with no products, and every rejected row and warning. */
  import { count } from '#lib/v2/format.js';

  type Report = {
    counts: { label: string; created: number; updated: number }[];
    notes: { label: string; count: number }[];
    empty_categories: string[];
    rejected: string[];
    warnings: string[];
  };
  let { report }: { report: Report } = $props();
</script>

<section class="v2-card result" aria-label="Import report">
  {#if report.counts.length}
    <table class="counts">
      <thead>
        <tr><th></th><th class="v2-r">New</th><th class="v2-r">Updated</th></tr>
      </thead>
      <tbody>
        {#each report.counts as row (row.label)}
          <tr>
            <td>{row.label}</td>
            <td class="v2-r v2-num">{count(row.created)}</td>
            <td class="v2-r v2-num">{count(row.updated)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  {#each report.notes as n (n.label)}
    <p class="v2-sub line">{n.label}: <span class="v2-num">{count(n.count)}</span></p>
  {/each}

  {#if report.empty_categories.length}
    <h3>Categories with no products</h3>
    <ul>
      {#each report.empty_categories as line (line)}<li>{line}</li>{/each}
    </ul>
  {/if}

  <details open={report.rejected.length > 0 && report.rejected.length <= 10}>
    <summary>Rejected rows: <span class="v2-num">{count(report.rejected.length)}</span></summary>
    <ul class="rows">
      {#each report.rejected as line (line)}<li>{line}</li>{/each}
    </ul>
  </details>
  <details>
    <summary>Warnings: <span class="v2-num">{count(report.warnings.length)}</span></summary>
    <ul class="rows">
      {#each report.warnings as line (line)}<li>{line}</li>{/each}
    </ul>
  </details>
</section>

<style>
  .result {
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  h3 {
    font-size: 12.5px;
    margin: 14px 0 4px;
  }
  .counts {
    border-collapse: collapse;
    font-size: 12.5px;
    margin-bottom: 8px;
  }
  .counts th,
  .counts td {
    padding: 4px 12px 4px 0;
    text-align: left;
  }
  .counts .v2-r {
    text-align: right;
  }
  .line {
    margin: 2px 0;
    font-size: 12.5px;
  }
  ul {
    margin: 4px 0 0;
    padding-left: 18px;
    font-size: 12.5px;
  }
  .rows {
    max-height: 24rem;
    overflow-y: auto;
  }
  details {
    margin-top: 10px;
    font-size: 12.5px;
  }
  summary {
    cursor: pointer;
  }
</style>
