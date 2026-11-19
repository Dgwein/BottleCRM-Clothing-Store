<script lang="ts">
  import { resolve } from '$app/paths';
  /**
   * One product: what describes all of it, then each variant with its size,
   * stock and one price, and where it is published. An admin edits all of it
   * here: the product, its characteristics, each variant, and new variants.
   *
   * Characteristics shown at the top hold for every variant; a variant row
   * lists only what sets it apart. The price is the one invoices quote on
   * every channel. Sizes are only the ones this product comes in.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import { money, count } from '#lib/v2/format.js';
  import { ChevronRight, Shirt } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();

  let product = $derived(data.product);
  let currencyOf = (c: string | null | undefined) => c || data.org.currency;
  const LISTING_STATUS: Record<string, string> = { ACTIVE: 'Active', INACTIVE: 'Inactive' };

  /** The message a form's last submit left, if it was this form's. */
  let note = (name: string) => (form?.form === name ? form : null);
  let picked = (value: unknown, option: string) =>
    Array.isArray(value) ? value.includes(option) : value === option;
</script>

<PageHeader title={product.name} record>
  {#snippet crumb()}
    <a href={resolve('invoices/catalog')}>Catalog</a>
    <ChevronRight size={12} />
    <span>{product.category.name}</span>
  {/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(data.variants.length)}</span>
    {data.variants.length === 1 ? 'variant' : 'variants'} in
    <span class="v2-num">{count(product.sizes.length)}</span>
    {product.sizes.length === 1 ? 'size' : 'sizes'} ·
    <span class="v2-num">{count(data.listings.length)}</span>
    {data.listings.length === 1 ? 'listing' : 'listings'}
    {#if !product.is_active}· <Pill tone="slate">Retired</Pill>{/if}
  {/snippet}
  {#snippet actions()}
    {#if data.can_manage}
      <ConfirmAction
        action="?/delete"
        label="Delete product"
        confirmLabel="Delete"
        explain="A product a deal, order or invoice sells is kept."
      />
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if note('delete')?.error}
      <p class="error" role="alert">{note('delete')?.error}</p>
    {/if}

    <div class="v2-label" style="margin-bottom:9px">Characteristics</div>
    {#if product.attributes.length}
      <div class="v2-card traits">
        {#each product.attributes as a (a.code)}
          <div class="trait">
            <span class="v2-sub">{a.name}</span>
            <span>{a.text}</span>
          </div>
        {/each}
      </div>
    {:else}
      <p class="v2-sub">No characteristics yet.</p>
    {/if}

    {#if data.can_manage && data.characteristics.length}
      <details class="v2-card editor" open={Boolean(note('characteristics'))}>
        <summary>Edit characteristics</summary>
        <form method="POST" action="?/characteristics" use:enhance class="grid">
          {#if note('characteristics')?.error}
            <p class="error full" role="alert">{note('characteristics')?.error}</p>
          {:else if note('characteristics')?.saved}
            <p class="v2-sub full" role="status">Saved</p>
          {/if}
          {#each data.characteristics as a (a.code)}
            <label class="v2-field">
              <span class="v2-label">{a.name}</span>
              {#if a.data_type === 'OPTION' && a.is_multivalued}
                <select class="v2-input" name="attr:{a.code}" multiple size="4">
                  {#each a.options as o (o.id)}
                    <option value={o.value} selected={picked(a.value, o.value)}>{o.value}</option>
                  {/each}
                </select>
              {:else if a.data_type === 'OPTION'}
                <select class="v2-input" name="attr:{a.code}">
                  <option value="">None</option>
                  {#each a.options as o (o.id)}
                    <option value={o.value} selected={picked(a.value, o.value)}>{o.value}</option>
                  {/each}
                </select>
              {:else if a.data_type === 'BOOLEAN'}
                <select class="v2-input" name="attr:{a.code}">
                  <option value="">None</option>
                  <option value="true" selected={a.value === true}>Yes</option>
                  <option value="false" selected={a.value === false}>No</option>
                </select>
              {:else if a.data_type === 'NUMBER'}
                <span class="pair">
                  <input
                    class="v2-input v2-num"
                    name="attr:{a.code}"
                    inputmode="decimal"
                    value={a.value === null ? '' : String(a.value)}
                  />
                  <input
                    class="v2-input unit"
                    name="unit:{a.code}"
                    value={a.value_unit}
                    aria-label="{a.name} unit"
                    maxlength="20"
                  />
                </span>
              {:else}
                <input
                  class="v2-input"
                  name="attr:{a.code}"
                  value={a.value === null ? '' : String(a.value)}
                  maxlength="500"
                />
              {/if}
            </label>
          {/each}
          <div class="full"><button class="v2-btn" type="submit">Save characteristics</button></div>
        </form>
      </details>
    {/if}

    <div class="v2-label" style="margin:20px 0 9px">Variants</div>
    {#if note('variants')?.saved}
      <p class="v2-sub" role="status">Variant deleted</p>
    {/if}
    {#if data.variants.length === 0}
      <EmptyState
        title="No variants yet"
        body="A variant is one size of this product in one color or design, with its own stock and price."
      >
        {#snippet icon()}<Shirt size={21} />{/snippet}
      </EmptyState>
    {:else}
      <div class="v2-table-wrap">
        <table class="v2-table">
          <thead>
            <tr>
              <th>Size</th>
              {#each data.axes as axis (axis.code)}
                <th>{axis.name}</th>
              {/each}
              <th>Listed as</th>
              <th class="v2-r">{data.can_manage ? 'Price, stock, size' : 'Stock'}</th>
              {#if !data.can_manage}<th class="v2-r">Price</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each data.variants as v (v.id)}
              <tr style={v.is_active ? '' : 'opacity:.62'}>
                <td data-m="title">
                  <span class="v2-table-primary">{v.size}</span>
                  {#if v.sku}<div class="v2-table-secondary v2-num">{v.sku}</div>{/if}
                  {#each v.attributes as a (a.code)}
                    <div class="v2-table-secondary">{a.name}: {a.text}</div>
                  {/each}
                </td>
                {#each data.axes as axis (axis.code)}
                  <td data-m="meta">{v.axes[axis.code] ?? ''}</td>
                {/each}
                <td data-m="meta">
                  {#each v.listings as l (l.id)}
                    <div class="listed">
                      <span class="v2-num">{l.listing_external_id}</span>
                      {#if l.listing_status && l.listing_status !== 'ACTIVE'}
                        <Pill tone="slate"
                          >{LISTING_STATUS[l.listing_status] ?? l.listing_status}</Pill
                        >
                      {/if}
                    </div>
                  {:else}
                    <span class="v2-sub">Not listed</span>
                  {/each}
                </td>
                {#if data.can_manage}
                  <td class="v2-r" data-m="bar">
                    <div class="row-actions">
                      <form method="POST" action="?/variant" use:enhance class="variant-form">
                        <input type="hidden" name="id" value={v.id} />
                        <input
                          class="v2-input v2-num price"
                          name="price"
                          inputmode="decimal"
                          required
                          value={v.price.toFixed(2)}
                          aria-label="Price of {v.name}"
                        />
                        <input
                          class="v2-input v2-num stock"
                          name="stock"
                          inputmode="numeric"
                          required
                          value={String(v.stock)}
                          aria-label="Stock of {v.name}"
                        />
                        <input
                          class="v2-input size"
                          name="size"
                          value={v.size}
                          maxlength="50"
                          aria-label="Size of {v.name}"
                        />
                        <select
                          class="v2-input"
                          name="is_active"
                          aria-label="Whether {v.name} sells"
                        >
                          <option value="true" selected={v.is_active}>Sells</option>
                          <option value="false" selected={!v.is_active}>Retired</option>
                        </select>
                        <button class="v2-btn" type="submit">Save</button>
                      </form>
                      <ConfirmAction
                        action="?/deleteVariant"
                        label="Delete"
                        confirmLabel="Delete variant"
                        hidden={{ id: v.id }}
                      />
                    </div>
                    {#if note(`variant:${v.id}`)?.error}
                      <p class="note error" role="alert">{note(`variant:${v.id}`)?.error}</p>
                    {:else if note(`variant:${v.id}`)?.saved}
                      <p class="note v2-sub" role="status">Saved</p>
                    {/if}
                  </td>
                {:else}
                  <td class="v2-r v2-num" data-m="meta">{count(v.stock)} in stock</td>
                  <td class="v2-r" data-m="meta">
                    <span class="v2-num" style="font-weight:600">
                      {money(v.price, currencyOf(v.currency))}
                    </span>
                    {#if !v.is_active}<Pill tone="slate">Retired</Pill>{/if}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    {#if data.can_manage}
      <details class="v2-card editor" open={Boolean(note('addVariant'))}>
        <summary>Add a variant</summary>
        <form method="POST" action="?/addVariant" use:enhance class="grid">
          {#if note('addVariant')?.error}
            <p class="error full" role="alert">{note('addVariant')?.error}</p>
          {:else if note('addVariant')?.saved}
            <p class="v2-sub full" role="status">Variant added</p>
          {/if}
          <label class="v2-field">
            <span class="v2-label">Size</span>
            <input class="v2-input" name="size" maxlength="50" placeholder="Unique" />
          </label>
          <label class="v2-field">
            <span class="v2-label">Price</span>
            <input class="v2-input v2-num" name="price" inputmode="decimal" required />
          </label>
          <label class="v2-field">
            <span class="v2-label">Stock</span>
            <input class="v2-input v2-num" name="stock" inputmode="numeric" value="0" />
          </label>
          <label class="v2-field">
            <span class="v2-label">SKU</span>
            <input class="v2-input" name="sku" maxlength="100" />
          </label>
          {#each data.axis_fields as axis (axis.code)}
            <label class="v2-field">
              <span class="v2-label">{axis.name}{axis.is_required ? '' : ' (optional)'}</span>
              <select class="v2-input" name="axis:{axis.code}" required={axis.is_required}>
                <option value="">Pick one</option>
                {#each axis.options as o (o.id)}
                  <option value={o.value}>{o.value}</option>
                {/each}
              </select>
            </label>
          {/each}
          <div class="full"><button class="v2-btn" type="submit">Add variant</button></div>
        </form>
      </details>
    {/if}

    {#if data.listings.length}
      <div class="v2-label" style="margin:20px 0 9px">Listings</div>
      <div class="v2-card" style="overflow:hidden">
        {#each data.listings as l (l.id)}
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>{l.channel} <span class="v2-num">{l.external_id}</span></b>
              <span class="v2-sub" style="font-size:11.5px">
                {l.title}{l.listing_type ? ` · ${l.listing_type}` : ''} ·
                <span class="v2-num">{count(l.variant_count)}</span>
                {l.variant_count === 1 ? 'variant' : 'variants'}
              </span>
            </div>
            {#if l.status}
              <Pill tone={l.status === 'ACTIVE' ? 'moss' : 'slate'}>
                {LISTING_STATUS[l.status] ?? l.status}
              </Pill>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    {#if data.can_manage}
      <details class="v2-card editor" open={Boolean(note('product'))}>
        <summary>Edit product</summary>
        <form method="POST" action="?/product" use:enhance class="grid">
          {#if note('product')?.error}
            <p class="error full" role="alert">{note('product')?.error}</p>
          {:else if note('product')?.saved}
            <p class="v2-sub full" role="status">Saved</p>
          {/if}
          <label class="v2-field full">
            <span class="v2-label">Name</span>
            <input class="v2-input" name="name" value={product.name} required maxlength="255" />
          </label>
          <label class="v2-field">
            <span class="v2-label">Status</span>
            <select class="v2-input" name="is_active">
              <option value="true" selected={product.is_active}>Sells</option>
              <option value="false" selected={!product.is_active}>Retired</option>
            </select>
          </label>
          <label class="v2-field full">
            <span class="v2-label">Description</span>
            <textarea class="v2-input" name="description" rows="5">{product.description}</textarea>
          </label>
          <div class="full"><button class="v2-btn" type="submit">Save product</button></div>
        </form>
      </details>
    {:else if product.description}
      <div class="v2-label" style="margin:20px 0 9px">Description</div>
      <p class="description">{product.description}</p>
    {/if}
  </div>
</div>

<style>
  .traits {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 10px 18px;
    padding: 14px 16px;
  }
  .trait {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 13px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .trait .v2-sub {
    font-size: 11.5px;
  }
  .editor {
    margin-top: 12px;
    padding: 12px 16px;
  }
  .editor summary {
    cursor: pointer;
    font-weight: 600;
    font-size: 13px;
    min-height: 28px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px 18px;
    margin-top: 12px;
  }
  .grid .v2-field {
    margin-bottom: 0;
  }
  .full {
    grid-column: 1 / -1;
  }
  .pair {
    display: flex;
    gap: 6px;
  }
  .pair .unit {
    width: 5rem;
  }
  .listed {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 8px;
    font-size: 12px;
  }
  .listed + .listed {
    margin-top: 4px;
  }
  .row-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    justify-content: flex-end;
  }
  .variant-form {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .variant-form select {
    width: auto;
  }
  .variant-form .price {
    width: 7rem;
    text-align: right;
  }
  .variant-form .stock {
    width: 4.5rem;
    text-align: right;
  }
  .variant-form .size {
    width: 7rem;
  }
  .error {
    color: var(--v2-rust);
    font-size: 12.5px;
    margin: 0 0 10px;
  }
  .note {
    font-size: 11.5px;
    margin: 4px 0 0;
  }
  .description {
    white-space: pre-line;
    font-size: 13px;
    max-width: 72ch;
    margin: 0;
  }
  @media (max-width: 768px) {
    .row-actions,
    .variant-form {
      flex-wrap: wrap;
      justify-content: flex-start;
    }
    .variant-form .v2-input,
    .variant-form .v2-btn,
    .grid .v2-input,
    .grid .v2-btn {
      min-height: 44px;
    }
    .variant-form .price,
    .variant-form .stock,
    .variant-form .size {
      flex: 1 1 5rem;
    }
  }
</style>
