<script lang="ts">
  import { resolve } from '$app/paths';
  /**
   * The catalog: one row per product as the store thinks of it, with the
   * sizes it comes in (only those: no product needs every size), its total
   * stock, and the spread of its sellable variants' prices.
   */
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import { money, count } from '#lib/v2/format.js';
  import { asInternalPath } from '#lib/utils/paths.js';
  import { Plus, Search, Shirt } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data }: PageProps = $props();

  let filtered = $derived(
    Boolean(data.filters.q || data.filters.category || data.filters.size || data.filters.in_stock)
  );

  function priceRange(p: (typeof data.products)[number]) {
    const currency = p.currency || data.org.currency;
    if (p.price_min === null || p.price_max === null) return 'No price';
    if (p.price_min === p.price_max) return money(p.price_min, currency);
    return `${money(p.price_min, currency)} to ${money(p.price_max, currency)}`;
  }
</script>

<PageHeader title="Catalog">
  {#snippet sub()}
    <span class="v2-num">{count(data.count)}</span>
    {data.count === 1 ? 'product' : 'products'}{filtered ? ' match' : ''}
  {/snippet}
  {#snippet actions()}
    {#if data.can_manage}
      <a class="v2-btn v2-btn-primary" href={resolve('invoices/catalog/new')}><Plus />New product</a
      >
    {/if}
  {/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-pad" style="padding-top:10px;flex:none">
  <form method="GET" class="filters" role="search">
    <input
      class="v2-input"
      type="search"
      name="q"
      value={data.filters.q}
      placeholder="Search by name, SKU or listing id"
      aria-label="Search the catalog"
      maxlength="200"
    />
    <select class="v2-input" name="category" aria-label="Category">
      <option value="">Every category</option>
      {#each data.categories as c (c.id)}
        <option value={c.id} selected={c.id === data.filters.category}>{c.name}</option>
      {/each}
    </select>
    <select class="v2-input" name="size" aria-label="Size">
      <option value="">Every size</option>
      {#each data.sizes as s (s)}
        <option value={s} selected={s === data.filters.size}>{s}</option>
      {/each}
      {#if data.filters.size && !data.sizes.includes(data.filters.size)}
        <option value={data.filters.size} selected>{data.filters.size}</option>
      {/if}
    </select>
    <select class="v2-input" name="in_stock" aria-label="Stock">
      <option value="">Any stock</option>
      <option value="true" selected={data.filters.in_stock === 'true'}>In stock</option>
      <option value="false" selected={data.filters.in_stock === 'false'}>Out of stock</option>
    </select>
    <button class="v2-btn" type="submit"><Search size={13} />Filter</button>
  </form>
</div>

<div class="v2-scroll">
  {#if data.products.length === 0}
    <EmptyState
      title={filtered ? 'Nothing matches' : 'The catalog is empty'}
      body={filtered
        ? 'No product matches these filters. Clear one to see more.'
        : 'Products, their variants and their prices arrive here from an import, such as a Mercado Libre report, or from New product.'}
    >
      {#snippet icon()}<Shirt size={21} />{/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap scroll">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Product</th>
            <th>Sizes</th>
            <th class="v2-r">Variants</th>
            <th class="v2-r">Stock</th>
            <th class="v2-r">Price</th>
            <th class="v2-r">Listings</th>
          </tr>
        </thead>
        <tbody>
          {#each data.products as p (p.id)}
            <tr style={p.is_active ? '' : 'opacity:.62'}>
              <td data-m="title" class="name">
                <a class="v2-row-link" href={resolve(`invoices/catalog/${p.id}`)}>
                  <div class="v2-table-primary">{p.name}</div>
                  <div class="v2-table-secondary">{p.category}</div>
                </a>
              </td>
              <td data-m="meta" class="sizes">{p.sizes.join(', ')}</td>
              <td class="v2-r v2-num" data-m="meta">
                {count(p.active_variant_count)}
                {#if p.active_variant_count !== p.variant_count}
                  <span class="v2-sub">of {count(p.variant_count)}</span>
                {/if}
                {p.variant_count === 1 ? 'variant' : 'variants'}
              </td>
              <td class="v2-r v2-num" data-m="meta">{count(p.stock)} in stock</td>
              <td class="v2-r v2-num" data-m="meta">
                {#if p.price_min === null}
                  <Pill tone="rust">No price</Pill>
                {:else}
                  {priceRange(p)}
                {/if}
              </td>
              <td class="v2-r v2-num" data-m="meta">
                {count(p.listing_count)}
                {p.listing_count === 1 ? 'listing' : 'listings'}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="v2-pad pager">
      <span class="v2-sub">
        <span class="v2-num">{count(data.from)}</span> to
        <span class="v2-num">{count(data.to)}</span>
        of <span class="v2-num">{count(data.count)}</span>
      </span>
      <span class="pager-links">
        {#if data.prev !== null}
          <a class="v2-btn" href={resolve(asInternalPath(`/invoices/catalog${data.prev}`))}
            >Previous</a
          >
        {/if}
        {#if data.next !== null}
          <a class="v2-btn" href={resolve(asInternalPath(`/invoices/catalog${data.next}`))}>Next</a>
        {/if}
      </span>
    </div>
  {/if}
</div>

<style>
  .filters {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .filters input.v2-input {
    flex: 1 1 14rem;
    min-width: 0;
    max-width: 28rem;
  }
  .filters select.v2-input {
    flex: 0 1 11rem;
    min-width: 0;
  }
  .scroll {
    overflow-x: auto;
  }
  .name {
    white-space: normal;
    min-width: 16rem;
  }
  .sizes {
    white-space: normal;
    min-width: 9rem;
    max-width: 16rem;
    padding-top: 6px;
    padding-bottom: 6px;
    font-size: 12px;
  }
  .pager {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-top: 12px;
    padding-bottom: 24px;
    font-size: 12px;
  }
  .pager-links {
    display: flex;
    gap: 8px;
  }
  @media (max-width: 768px) {
    .filters .v2-input,
    .filters .v2-btn,
    .pager .v2-btn {
      min-height: 44px;
    }
    .filters input.v2-input,
    .filters select.v2-input {
      flex: 1 1 100%;
      max-width: none;
    }
  }
</style>
