<script lang="ts">
  /**
   * Categories and the attributes each uses. A variant axis splits a product
   * into variants (with the size, every variant's own); a characteristic
   * describes it. Reps see the list; an admin changes it.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import { count } from '#lib/v2/format.js';
  import { FolderTree } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let note = (name: string) => (form?.form === name ? form : null);
</script>

<PageHeader title="Categories">
  {#snippet sub()}
    <span class="v2-num">{count(data.categories.length)}</span>
    {data.categories.length === 1 ? 'category' : 'categories'}
  {/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if data.can_manage}
      <form method="POST" action="?/create" use:enhance class="v2-card inline">
        <input
          class="v2-input"
          name="name"
          required
          maxlength="150"
          placeholder="New category"
          aria-label="New category name"
        />
        <button class="v2-btn v2-btn-primary" type="submit">Add category</button>
        {#if note('create')?.error}
          <p class="error" role="alert">{note('create')?.error}</p>
        {/if}
      </form>
    {/if}

    {#if data.categories.length === 0}
      <EmptyState
        title="No categories yet"
        body="A category groups products and says which attributes describe them."
      >
        {#snippet icon()}<FolderTree size={21} />{/snippet}
      </EmptyState>
    {/if}

    {#each data.categories as c (c.id)}
      <section class="v2-card category" aria-label={c.name}>
        <header>
          <h2>{c.name}</h2>
          <span class="v2-sub">
            <span class="v2-num">{count(c.product_count)}</span>
            {c.product_count === 1 ? 'product' : 'products'}
          </span>
          {#if data.can_manage}
            <ConfirmAction
              action="?/delete"
              label="Delete"
              confirmLabel="Delete category"
              hidden={{ id: c.id }}
            />
          {/if}
        </header>
        {#if note(`category:${c.id}`)?.error}
          <p class="error" role="alert">{note(`category:${c.id}`)?.error}</p>
        {:else if note(`category:${c.id}`)?.saved}
          <p class="v2-sub" role="status">Saved</p>
        {/if}
        <ul class="links">
          {#each c.links as l (l.id)}
            <li>
              <span>{l.name}</span>
              <Pill tone={l.role === 'VARIANT_AXIS' ? 'ink' : 'slate'}>
                {l.role === 'VARIANT_AXIS' ? 'Variant axis' : 'Characteristic'}
              </Pill>
              {#if l.is_required}<Pill tone="clay">Required</Pill>{/if}
              {#if data.can_manage}
                <form method="POST" action="?/unlink" use:enhance>
                  <input type="hidden" name="id" value={l.id} />
                  <input type="hidden" name="category" value={c.id} />
                  <button
                    class="v2-btn v2-btn-sm"
                    type="submit"
                    aria-label="Remove {l.name} from {c.name}">Remove</button
                  >
                </form>
              {/if}
            </li>
          {:else}
            <li class="v2-sub">No attributes yet.</li>
          {/each}
        </ul>
        {#if data.can_manage}
          <div class="forms">
            <form method="POST" action="?/rename" use:enhance class="inline">
              <input type="hidden" name="id" value={c.id} />
              <input
                class="v2-input"
                name="name"
                value={c.name}
                required
                maxlength="150"
                aria-label="Name of {c.name}"
              />
              <button class="v2-btn" type="submit">Rename</button>
            </form>
            <form method="POST" action="?/link" use:enhance class="inline">
              <input type="hidden" name="id" value={c.id} />
              <select
                class="v2-input"
                name="attribute"
                required
                aria-label="Attribute to add to {c.name}"
              >
                <option value="">Add an attribute</option>
                {#each data.attributes.filter((a) => !c.links.some((l) => l.attribute === a.id)) as a (a.id)}
                  <option value={a.id}>{a.name}</option>
                {/each}
              </select>
              <select class="v2-input" name="role" aria-label="How {c.name} uses it">
                <option value="CHARACTERISTIC">Characteristic</option>
                <option value="VARIANT_AXIS">Variant axis</option>
              </select>
              <select class="v2-input" name="is_required" aria-label="Whether it is required">
                <option value="false">Optional</option>
                <option value="true">Required</option>
              </select>
              <button class="v2-btn" type="submit">Add</button>
            </form>
          </div>
        {/if}
      </section>
    {/each}
  </div>
</div>

<style>
  .inline {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  form.v2-card {
    padding: 12px 16px;
    margin-bottom: 16px;
  }
  .category {
    padding: 12px 16px;
    margin-bottom: 14px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  h2 {
    font-size: 14px;
    margin: 0;
  }
  .links {
    list-style: none;
    padding: 0;
    margin: 10px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
  }
  .links li {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .forms {
    display: flex;
    flex-direction: column;
    gap: 8px;
    border-top: 1px solid var(--v2-line-soft);
    padding-top: 10px;
  }
  .error {
    color: var(--v2-rust);
    font-size: 12.5px;
    margin: 6px 0;
    flex-basis: 100%;
  }
  @media (max-width: 768px) {
    .v2-input,
    .v2-btn {
      min-height: 44px;
    }
    .inline .v2-input {
      flex: 1 1 100%;
    }
  }
</style>
