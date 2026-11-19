<script lang="ts">
  /**
   * Where the store sells: online channels and physical stores. A channel's
   * code is how imports and mappings find it, so it is set once; everything
   * else, its kind included, can change while it has no listings.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SectionTabs from '#lib/v2/components/SectionTabs.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import { count } from '#lib/v2/format.js';
  import { COUNTRIES } from '#lib/constants/countries.js';
  import { Store } from '@lucide/svelte';
  import ChannelFields from './ChannelFields.svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let note = (name: string) => (form?.form === name ? form : null);
  const countryName = (code: string) => COUNTRIES.find((c) => c.code === code)?.name ?? code;
  const where = (c: (typeof data.channels)[number]) =>
    [c.address_line, c.city, c.state, c.postcode, c.country ? countryName(c.country) : '']
      .filter(Boolean)
      .join(', ');
</script>

<PageHeader title="Sales channels">
  {#snippet sub()}
    <span class="v2-num">{count(data.channels.length)}</span>
    {data.channels.length === 1 ? 'channel' : 'channels'}
  {/snippet}
</PageHeader>

<SectionTabs set="catalog" />

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if data.can_manage}
      <form method="POST" action="?/create" use:enhance class="v2-card inline">
        <ChannelFields label="the new channel" kinds={data.kinds} />
        <input
          class="v2-input short"
          name="code"
          required
          maxlength="50"
          pattern="[a-z0-9_\-]+"
          placeholder="code (e.g. amazon)"
          aria-label="Code of the new channel"
        />
        <button class="v2-btn v2-btn-primary" type="submit">Add channel</button>
        {#if note('create')?.error}
          <p class="error" role="alert">{note('create')?.error}</p>
        {/if}
      </form>
    {/if}

    {#if data.channels.length === 0}
      <EmptyState
        title="No sales channels yet"
        body="Add the online channels and physical stores the store sells through, or import a Mercado Libre report."
      >
        {#snippet icon()}<Store size={21} />{/snippet}
      </EmptyState>
    {/if}

    {#each data.channels as c (c.id)}
      <section class="v2-card channel" aria-label={c.name}>
        <header>
          <h2>{c.name}</h2>
          <Pill tone={c.kind === 'STORE' ? 'clay' : 'ink'}
            >{c.kind === 'STORE' ? 'Physical store' : 'Online'}</Pill
          >
          <span class="v2-sub v2-num">{c.code}</span>
          {#if c.site}<span class="v2-sub">market {c.site}</span>{/if}
          <Pill tone={c.is_active ? 'moss' : 'slate'}
            >{c.is_active ? 'In use' : 'Switched off'}</Pill
          >
          {#if data.can_manage}
            <ConfirmAction
              action="?/delete"
              label="Delete"
              confirmLabel="Delete channel"
              hidden={{ id: c.id }}
            />
          {/if}
        </header>
        {#if c.kind === 'STORE' && (where(c) || c.phone)}
          <p class="v2-sub where">
            {where(c)}{#if c.phone}{where(c) ? ' · ' : ''}<span class="v2-num">{c.phone}</span>{/if}
          </p>
        {/if}
        {#if note(`channel:${c.id}`)?.error}
          <p class="error" role="alert">{note(`channel:${c.id}`)?.error}</p>
        {:else if note(`channel:${c.id}`)?.saved}
          <p class="v2-sub" role="status">Saved</p>
        {/if}
        {#if data.can_manage}
          <form method="POST" action="?/update" use:enhance class="edit inline">
            <input type="hidden" name="id" value={c.id} />
            <ChannelFields values={c} label={c.name} kinds={data.kinds} />
            <select class="v2-input" name="is_active" aria-label="Whether {c.name} is in use">
              <option value="true" selected={c.is_active}>In use</option>
              <option value="false" selected={!c.is_active}>Switched off</option>
            </select>
            <button class="v2-btn" type="submit">Save</button>
          </form>
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
  .inline :global(.v2-input) {
    flex: 1 1 10rem;
    min-width: 0;
  }
  .inline .short {
    flex: 0 1 10rem;
  }
  form.v2-card {
    padding: 12px 16px;
    margin-bottom: 16px;
  }
  .channel {
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
  .where {
    margin: 6px 0 0;
    font-size: 12.5px;
  }
  .edit {
    border-top: 1px solid var(--v2-line-soft);
    padding-top: 10px;
    margin-top: 10px;
  }
  .error {
    color: var(--v2-rust);
    font-size: 12.5px;
    margin: 6px 0;
    flex-basis: 100%;
  }
  @media (max-width: 768px) {
    .inline :global(.v2-input),
    .v2-btn {
      min-height: 44px;
    }
    .inline :global(.v2-input) {
      flex: 1 1 100%;
    }
  }
</style>
