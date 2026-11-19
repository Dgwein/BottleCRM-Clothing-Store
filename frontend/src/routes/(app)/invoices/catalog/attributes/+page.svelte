<script lang="ts">
  /**
   * The attributes products are described with. One attribute serves every
   * category that uses it (material is one attribute whether Mercado Libre
   * calls it MAIN_MATERIAL or MATERIALS), so renaming it or an option renames
   * it everywhere.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import { count } from '#lib/v2/format.js';
  import { Tags } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let note = (name: string) => (form?.form === name ? form : null);
  let typeLabel = (value: string) => data.data_types.find((t) => t.value === value)?.label ?? value;
</script>

<PageHeader title="Attributes">
  {#snippet sub()}
    <span class="v2-num">{count(data.attributes.length)}</span>
    {data.attributes.length === 1 ? 'attribute' : 'attributes'}
  {/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if data.can_manage}
      <form method="POST" action="?/create" use:enhance class="v2-card inline">
        <input
          class="v2-input"
          name="code"
          required
          maxlength="64"
          pattern="[a-z0-9_\-]+"
          placeholder="code (e.g. neck_type)"
          aria-label="New attribute code"
        />
        <input
          class="v2-input"
          name="name"
          required
          maxlength="150"
          placeholder="Name"
          aria-label="New attribute name"
        />
        <select class="v2-input" name="data_type" aria-label="New attribute type">
          {#each data.data_types as t (t.value)}
            <option value={t.value}>{t.label}</option>
          {/each}
        </select>
        <select class="v2-input" name="is_multivalued" aria-label="One value or several">
          <option value="false">One value</option>
          <option value="true">Several values</option>
        </select>
        <input
          class="v2-input unit"
          name="unit"
          maxlength="20"
          placeholder="Unit"
          aria-label="New attribute unit"
        />
        <button class="v2-btn v2-btn-primary" type="submit">Add attribute</button>
        {#if note('create')?.error}
          <p class="error" role="alert">{note('create')?.error}</p>
        {/if}
      </form>
    {/if}

    {#if data.attributes.length === 0}
      <EmptyState
        title="No attributes yet"
        body="Attributes describe products: material, sleeve type, occasion."
      >
        {#snippet icon()}<Tags size={21} />{/snippet}
      </EmptyState>
    {/if}

    {#each data.attributes as a (a.id)}
      <section class="v2-card attribute" aria-label={a.name}>
        <header>
          <h2>{a.name}</h2>
          <span class="v2-sub v2-num">{a.code}</span>
          <Pill tone="slate"
            >{typeLabel(a.data_type)}{a.is_multivalued ? ', several values' : ''}</Pill
          >
          {#if a.unit}<span class="v2-sub">in {a.unit}</span>{/if}
          {#if data.can_manage}
            <ConfirmAction
              action="?/delete"
              label="Delete"
              confirmLabel="Delete attribute"
              hidden={{ id: a.id }}
            />
          {/if}
        </header>
        {#if a.channel_codes.length}
          <p class="v2-sub codes">From {a.channel_codes.join(', ')}</p>
        {/if}
        {#if note(`attribute:${a.id}`)?.error}
          <p class="error" role="alert">{note(`attribute:${a.id}`)?.error}</p>
        {:else if note(`attribute:${a.id}`)?.saved}
          <p class="v2-sub" role="status">Saved</p>
        {/if}
        {#if a.data_type === 'OPTION'}
          <ul class="options">
            {#each a.options as o (o.id)}
              <li>
                {#if data.can_manage}
                  <form method="POST" action="?/renameOption" use:enhance class="inline">
                    <input type="hidden" name="id" value={o.id} />
                    <input type="hidden" name="attribute" value={a.id} />
                    <input
                      class="v2-input"
                      name="value"
                      value={o.value}
                      required
                      maxlength="150"
                      aria-label="Option {o.value}"
                    />
                    <button class="v2-btn v2-btn-sm" type="submit">Rename</button>
                  </form>
                  <form method="POST" action="?/deleteOption" use:enhance>
                    <input type="hidden" name="id" value={o.id} />
                    <input type="hidden" name="attribute" value={a.id} />
                    <button
                      class="v2-btn v2-btn-sm"
                      type="submit"
                      aria-label="Delete option {o.value}">Delete</button
                    >
                  </form>
                {:else}
                  <span>{o.value}</span>
                {/if}
              </li>
            {:else}
              <li class="v2-sub">No options yet.</li>
            {/each}
          </ul>
        {/if}
        {#if data.can_manage}
          <div class="forms">
            <form method="POST" action="?/update" use:enhance class="inline">
              <input type="hidden" name="id" value={a.id} />
              <input
                class="v2-input"
                name="name"
                value={a.name}
                required
                maxlength="150"
                aria-label="Name of {a.name}"
              />
              <input
                class="v2-input unit"
                name="unit"
                value={a.unit}
                maxlength="20"
                aria-label="Unit of {a.name}"
              />
              <button class="v2-btn" type="submit">Save</button>
            </form>
            {#if a.data_type === 'OPTION'}
              <form method="POST" action="?/addOption" use:enhance class="inline">
                <input type="hidden" name="id" value={a.id} />
                <input
                  class="v2-input"
                  name="value"
                  required
                  maxlength="150"
                  placeholder="New option"
                  aria-label="New option for {a.name}"
                />
                <button class="v2-btn" type="submit">Add option</button>
              </form>
            {/if}
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
  .attribute {
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
  .codes {
    font-size: 11.5px;
    margin: 6px 0 0;
  }
  .options {
    list-style: none;
    padding: 0;
    margin: 10px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
  }
  .options li {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .unit {
    width: 6rem;
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
