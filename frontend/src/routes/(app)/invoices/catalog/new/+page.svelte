<script lang="ts">
  import { resolve } from '$app/paths';
  /**
   * A new product: name, category and description. Its characteristics and
   * variants are added on its own page next, since the category decides
   * which apply.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import { ChevronRight } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let values = $derived(form?.values ?? { name: '', category: '', description: '' });
</script>

<PageHeader title="New product" record center width="62ch">
  {#snippet crumb()}
    <a href={resolve('invoices/catalog')}>Catalog</a>
    <ChevronRight size={12} />
    <span>New product</span>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <form
    method="POST"
    action="?/create"
    use:enhance
    class="v2-pad"
    style="padding-top:18px;padding-bottom:36px;max-width:62ch;margin-left:auto;margin-right:auto"
  >
    {#if form?.error}
      <p class="error" role="alert">{form.error}</p>
    {/if}
    <label class="v2-field">
      <span class="v2-label">Name</span>
      <input class="v2-input" name="name" value={values.name} required maxlength="255" />
    </label>
    <label class="v2-field">
      <span class="v2-label">Category</span>
      <select class="v2-input" name="category" required>
        <option value="">Pick a category</option>
        {#each data.categories as c (c.id)}
          <option value={c.id} selected={c.id === values.category}>{c.name}</option>
        {/each}
      </select>
    </label>
    <label class="v2-field">
      <span class="v2-label">Description</span>
      <textarea class="v2-input" name="description" rows="5">{values.description}</textarea>
    </label>
    <div class="actions">
      <button class="v2-btn v2-btn-primary" type="submit">Add product</button>
      <a class="v2-btn" href={resolve('invoices/catalog')}>Cancel</a>
    </div>
  </form>
</div>

<style>
  .error {
    color: var(--v2-rust);
    font-size: 12.5px;
    margin: 0 0 14px;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  @media (max-width: 768px) {
    .v2-input,
    .v2-btn {
      min-height: 44px;
    }
  }
</style>
