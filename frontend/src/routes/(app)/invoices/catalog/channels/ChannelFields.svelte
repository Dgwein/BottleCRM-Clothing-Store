<script lang="ts">
  /**
   * A sales channel's fields, shared by the add form and each edit form. An
   * online channel has a market id; a physical store has an address and a
   * phone. Only the fields of the chosen kind are shown; the server sends
   * the other kind's empty.
   */
  import { COUNTRIES } from '#lib/constants/countries.js';

  type Values = {
    name?: string;
    kind?: string;
    site?: string;
    address_line?: string;
    city?: string;
    state?: string;
    postcode?: string;
    country?: string;
    phone?: string;
  };
  let {
    values = {},
    label,
    kinds
  }: {
    values?: Values;
    label: string;
    kinds: readonly { value: string; label: string }[];
  } = $props();

  // Seeded from the stored kind; the select then drives which fields show.
  // svelte-ignore state_referenced_locally
  let kind = $state(values.kind ?? 'ONLINE');
</script>

<input
  class="v2-input"
  name="name"
  value={values.name ?? ''}
  required
  maxlength="100"
  placeholder="Name (e.g. Amazon, Tienda Centro)"
  aria-label="Name of {label}"
/>
<select class="v2-input" name="kind" bind:value={kind} aria-label="Kind of {label}">
  {#each kinds as k (k.value)}
    <option value={k.value}>{k.label}</option>
  {/each}
</select>
{#if kind === 'STORE'}
  <input
    class="v2-input wide"
    name="address_line"
    value={values.address_line ?? ''}
    maxlength="255"
    placeholder="Address"
    aria-label="Address of {label}"
  />
  <input
    class="v2-input"
    name="city"
    value={values.city ?? ''}
    maxlength="255"
    placeholder="City"
    aria-label="City of {label}"
  />
  <input
    class="v2-input"
    name="state"
    value={values.state ?? ''}
    maxlength="255"
    placeholder="State"
    aria-label="State of {label}"
  />
  <input
    class="v2-input short"
    name="postcode"
    value={values.postcode ?? ''}
    maxlength="64"
    placeholder="Postal code"
    aria-label="Postal code of {label}"
  />
  <select class="v2-input" name="country" aria-label="Country of {label}">
    <option value="" selected={!values.country}>Country</option>
    {#each COUNTRIES as c (c.code)}
      <option value={c.code} selected={c.code === values.country}>{c.name}</option>
    {/each}
  </select>
  <input
    class="v2-input"
    name="phone"
    type="tel"
    value={values.phone ?? ''}
    maxlength="25"
    placeholder="Phone"
    aria-label="Phone of {label}"
  />
{:else}
  <input
    class="v2-input"
    name="site"
    value={values.site ?? ''}
    maxlength="50"
    placeholder="Market id (e.g. MLM)"
    aria-label="Market id of {label}"
  />
{/if}

<style>
  .wide {
    flex: 2 1 16rem;
  }
  .short {
    flex: 0 1 8rem;
  }
</style>
