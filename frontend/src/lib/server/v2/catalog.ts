/**
 * The catalog: products with variants, their characteristics, categories,
 * attributes, the sales channels they are sold through (online channels and
 * physical stores) and the import of a channel's report (`/api/catalog/`).
 *
 * A catalog product is what the store sells as one thing (a dress); its
 * variants are the sellable rows line items pick. Each variant has a size and
 * a stock of its own (columns), its other axes (color, design), and one price,
 * the one invoices quote, whatever channel sells it. Characteristics are
 * attributes stored only where a product has them, so they arrive as a list.
 *
 * Reading is open to every member; every change is an admin act, enforced by
 * the API. `can_manage` only decides whether a page offers the forms.
 *
 * The `parse*` functions read a submitted form into an API payload, or into
 * the sentence that says what is wrong with it. They are the web's own
 * validation, run before anything is sent; the API checks everything again.
 */
import { fail } from '@sveltejs/kit';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import { apiErrorBody, apiStatus, firstMessage } from '#lib/api-errors.js';
import { readableError } from './form-errors.js';
import { viewerIsAdmin } from './organization.js';
import type { Cookies } from '@sveltejs/kit';
import type { components } from '#lib/api-schema.js';

type Schemas = components['schemas'];
type Ctx = { cookies: Cookies };

export const PAGE_SIZE = 50;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODE = /^[a-z0-9_-]{1,64}$/;
export const DATA_TYPES = [
  { value: 'OPTION', label: 'Option list' },
  { value: 'TEXT', label: 'Text' },
  { value: 'NUMBER', label: 'Number' },
  { value: 'BOOLEAN', label: 'Yes or no' }
] as const;
export const ROLES = [
  { value: 'CHARACTERISTIC', label: 'Characteristic' },
  { value: 'VARIANT_AXIS', label: 'Variant axis' }
] as const;

/**
 * What went wrong with a call, as a sentence: DRF's `detail` (a refusal), or
 * the first message of a per-field refusal, which the catalog API sends bare
 * (`{"attributes": ["Another variant ..."]}`).
 */
export function catalogError(err: unknown, fallback: string): string {
  const body = apiErrorBody(err);
  const detail = body?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (body && body.errors === undefined && body.message === undefined) {
    for (const value of Object.values(body)) {
      const first = firstMessage(value);
      if (typeof first === 'string' && first.trim()) return first.trim();
    }
  }
  return readableError(err, fallback);
}

/**
 * Run one write for a form action. Returns null when it worked, else the
 * `fail()` to return: 403 for a member, 404 for a row that is gone, 409 for
 * a refusal (something still uses it), 400 with the API's reason otherwise.
 * `form` names which form on the page the message belongs beside.
 */
export async function attempt(work: () => Promise<unknown>, fallback: string, form: string) {
  try {
    await work();
    return null;
  } catch (err) {
    const status = apiStatus(err);
    if (status === 403) {
      return fail(403, { form, error: 'Only an administrator can change the catalog.' });
    }
    if (status === 404) return fail(404, { form, error: 'That is no longer in the catalog.' });
    return fail(status === 409 ? 409 : 400, { form, error: catalogError(err, fallback) });
  }
}

type Parsed<T> = { ok: true; payload: T } | { ok: false; error: string };
const text = (form: FormData, key: string) => (form.get(key) ?? '').toString().trim();

// Lists and filters --------------------------------------------------------

export type CatalogFilters = {
  q: string;
  category: string;
  size: string;
  in_stock: '' | 'true' | 'false';
  offset: number;
};

/** The list's filters from the URL, trimmed to what the API accepts. */
export function readCatalogFilters(url: URL): CatalogFilters {
  const get = (key: string) => (url.searchParams.get(key) ?? '').trim();
  const category = get('category');
  const offset = Number.parseInt(get('offset'), 10);
  const stock = get('in_stock');
  return {
    q: get('q').slice(0, 200),
    category: UUID.test(category) ? category : '',
    size: get('size').slice(0, 100),
    in_stock: stock === 'true' || stock === 'false' ? stock : '',
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0
  };
}

/** The API query for one page of the list. */
export function catalogQuery(filters: CatalogFilters): URLSearchParams {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(filters.offset) });
  if (filters.q) params.set('search', filters.q);
  if (filters.category) params.set('category', filters.category);
  if (filters.size) params.set('size', filters.size);
  if (filters.in_stock) params.set('in_stock', filters.in_stock);
  return params;
}

/** The query string of another page of the list, keeping the filters. */
export function pageQuery(filters: CatalogFilters, offset: number): string {
  const parts = [
    ['q', filters.q],
    ['category', filters.category],
    ['size', filters.size],
    ['in_stock', filters.in_stock],
    ['offset', offset > 0 ? String(offset) : '']
  ]
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

/**
 * One characteristic as text. Lists join with commas, yes/no reads as words,
 * and a number keeps its unit ("35 cm").
 */
export function attributeText(value: unknown, unit = ''): string {
  if (Array.isArray(value)) return value.map((v) => attributeText(v)).join(', ');
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (value === null || value === undefined) return '';
  const shown = String(value);
  return unit ? `${shown} ${unit}` : shown;
}

function toRow(p: Schemas['CatalogProductList']) {
  return {
    id: p.id,
    name: p.name,
    category: p.category.name,
    is_active: p.is_active,
    variant_count: p.variant_count,
    active_variant_count: p.active_variant_count,
    stock: p.stock,
    sizes: p.sizes,
    listing_count: p.listing_count,
    price_min: p.price_min === null ? null : Number(p.price_min),
    price_max: p.price_max === null ? null : Number(p.price_max),
    currency: p.currency
  };
}

export async function listCatalog({ cookies }: Ctx, filters: CatalogFilters) {
  const [page, categories, sizes] = await Promise.all([
    apiRequest<Schemas['CatalogProductPage']>(
      `/catalog/products/?${catalogQuery(filters)}`,
      {},
      { cookies }
    ),
    apiRequest<Schemas['CategoryList']>('/catalog/categories/', {}, { cookies }),
    apiRequest<Schemas['CatalogSizeList']>('/catalog/sizes/', {}, { cookies })
  ]);
  return {
    results: page.results.map(toRow),
    count: page.count,
    categories: categories.results.map((c) => ({
      id: c.id,
      name: c.name,
      product_count: c.product_count
    })),
    sizes: sizes.results.map((row) => row.size),
    can_manage: viewerIsAdmin(cookies)
  };
}

// One product ---------------------------------------------------------------

function toListing(l: Schemas['VariantListing']) {
  return {
    id: l.id,
    channel: l.channel,
    listing_external_id: l.listing_external_id,
    listing_status: l.listing_status,
    external_product_id: l.external_product_id
  };
}

function toAttribute(a: Schemas['Attribute']) {
  return {
    id: a.id,
    code: a.code,
    name: a.name,
    data_type: a.data_type,
    is_multivalued: a.is_multivalued,
    unit: a.unit ?? '',
    options: a.options.map((o) => ({ id: o.id, value: o.value, sort_order: o.sort_order ?? 0 }))
  };
}

export type CatalogAttribute = ReturnType<typeof toAttribute>;

export async function getCatalogProduct({ cookies }: Ctx, id: string) {
  const [p, categories, attributes] = await Promise.all([
    apiRequest<Schemas['CatalogProduct']>(`/catalog/products/${id}/`, {}, { cookies }),
    apiRequest<Schemas['CategoryList']>('/catalog/categories/', {}, { cookies }),
    apiRequest<Schemas['AttributeList']>('/catalog/attributes/', {}, { cookies })
  ]);
  const byId = new Map(attributes.results.map((a) => [a.id, toAttribute(a)]));
  const category = categories.results.find((c) => c.id === p.category.id);
  const links = (category?.attributes ?? [])
    .map((link) => ({ link, attribute: byId.get(link.attribute) }))
    .filter((x): x is { link: Schemas['CategoryAttribute']; attribute: CatalogAttribute } =>
      Boolean(x.attribute)
    );
  const current = Object.fromEntries(p.attributes.map((a) => [a.code, a]));
  return {
    can_manage: viewerIsAdmin(cookies),
    product: {
      id: p.id,
      name: p.name,
      description: p.description,
      category: p.category,
      is_active: p.is_active,
      sizes: p.sizes,
      attributes: p.attributes.map((a) => ({
        code: a.code,
        name: a.name,
        text: attributeText(a.value, a.unit)
      }))
    },
    /** The category's characteristics, with what this product says for each. */
    characteristics: links
      .filter(({ link }) => link.role === 'CHARACTERISTIC')
      .map(({ attribute }) => ({
        ...attribute,
        value: current[attribute.code]?.value ?? null,
        value_unit: current[attribute.code]?.unit ?? attribute.unit
      })),
    /** The category's other axes (size is a column), for the new-variant form. */
    axis_fields: links
      .filter(({ link }) => link.role === 'VARIANT_AXIS')
      .map(({ link, attribute }) => ({ ...attribute, is_required: link.is_required })),
    axes: p.axes.map((a) => ({ code: a.code, name: a.name })),
    variants: p.variants.map((v) => ({
      id: v.id,
      name: v.name,
      sku: v.sku ?? '',
      size: v.size,
      stock: v.stock,
      axes: v.axes,
      price: Number(v.price),
      currency: v.currency,
      is_active: v.is_active,
      attributes: v.attributes.map((a) => ({
        code: a.code,
        name: a.name,
        text: attributeText(a.value, a.unit)
      })),
      listings: v.listings.map(toListing)
    })),
    listings: p.listings.map((l) => ({
      id: l.id,
      channel: l.channel_name,
      external_id: l.external_id,
      title: l.title,
      status: l.status,
      listing_type: l.listing_type,
      variant_count: l.variant_count
    }))
  };
}

// Form parsing (the web's validation) ----------------------------------------

/** A price is a number above 0 with at most two decimals: no variant is unpriced. */
export function priceOk(value: string): boolean {
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return false;
  const n = Number(value);
  return n > 0 && n < 1e10;
}

/** Stock is a whole number of 0 or more. */
export function stockOk(value: string): boolean {
  return /^\d{1,9}$/.test(value);
}

export function parseProductForm(form: FormData, { creating }: { creating: boolean }) {
  const name = text(form, 'name');
  const category = text(form, 'category');
  const description = text(form, 'description');
  if (!name) return { ok: false, error: 'Give the product a name.' } as const;
  if (name.length > 255)
    return { ok: false, error: 'Keep the name under 255 characters.' } as const;
  if (creating && !UUID.test(category)) {
    return { ok: false, error: 'Pick the category the product belongs to.' } as const;
  }
  const payload: FormBody<Schemas['CatalogProductWriteRequest']> = { name, description };
  if (creating) payload.category = category;
  else payload.is_active = form.get('is_active') === 'true';
  return { ok: true, payload } as const;
}

/**
 * A variant's columns and axes. Size is free text (the API spells it the
 * store's way, "Unique" when empty); each required axis needs an option.
 */
export function parseVariantForm(
  form: FormData,
  axisFields: { code: string; name: string; is_required: boolean; options: { value: string }[] }[]
): Parsed<FormBody<Schemas['CatalogVariantWriteRequest']>> {
  const price = text(form, 'price');
  const stock = text(form, 'stock') || '0';
  const size = text(form, 'size');
  const sku = text(form, 'sku');
  if (!priceOk(price)) return { ok: false, error: 'Enter a price above 0, like 499.00.' };
  if (!stockOk(stock)) return { ok: false, error: 'Enter the stock as a whole number, 0 or more.' };
  if (size.length > 50) return { ok: false, error: 'Keep the size under 50 characters.' };
  if (sku.length > 100) return { ok: false, error: 'Keep the SKU under 100 characters.' };
  const attributes: Record<string, string> = {};
  for (const axis of axisFields) {
    const value = text(form, `axis:${axis.code}`);
    if (!value) {
      if (axis.is_required) return { ok: false, error: `Pick a ${axis.name.toLowerCase()}.` };
      continue;
    }
    if (!axis.options.some((o) => o.value === value)) {
      return { ok: false, error: `${value} is not a ${axis.name.toLowerCase()} option.` };
    }
    attributes[axis.code] = value;
  }
  return {
    ok: true,
    payload: { price, stock: Number(stock), size, sku: sku || null, attributes }
  };
}

/** The row form on a variant: price, stock, size and whether it sells. */
export function parseVariantRow(
  form: FormData
): Parsed<FormBody<Schemas['PatchedCatalogVariantWriteRequest']>> {
  const price = text(form, 'price');
  const stock = text(form, 'stock');
  const size = text(form, 'size');
  if (!priceOk(price)) return { ok: false, error: 'Enter a price above 0, like 499.00.' };
  if (!stockOk(stock)) return { ok: false, error: 'Enter the stock as a whole number, 0 or more.' };
  if (size.length > 50) return { ok: false, error: 'Keep the size under 50 characters.' };
  return {
    ok: true,
    payload: { price, stock: Number(stock), size, is_active: form.get('is_active') === 'true' }
  };
}

/**
 * The characteristics form: one field per characteristic, named
 * `attr:<code>` (all of a list's checked options, `unit:<code>` beside a
 * number). An empty field removes the characteristic: nothing is stored for
 * one a product does not have.
 */
export function parseCharacteristics(
  form: FormData,
  attributes: CatalogAttribute[]
): Parsed<Record<string, unknown>> {
  const payload: Record<string, unknown> = {};
  for (const a of attributes) {
    const key = `attr:${a.code}`;
    if (a.data_type === 'OPTION' && a.is_multivalued) {
      const picked = form.getAll(key).map((v) => v.toString());
      const unknown = picked.find((v) => !a.options.some((o) => o.value === v));
      if (unknown) return { ok: false, error: `${unknown} is not a ${a.name} option.` };
      payload[a.code] = picked.length ? picked : null;
      continue;
    }
    const value = text(form, key);
    if (!value) {
      payload[a.code] = null;
    } else if (a.data_type === 'OPTION') {
      if (!a.options.some((o) => o.value === value)) {
        return { ok: false, error: `${value} is not a ${a.name} option.` };
      }
      payload[a.code] = value;
    } else if (a.data_type === 'NUMBER') {
      if (!/^-?\d+([.,]\d+)?$/.test(value))
        return { ok: false, error: `${a.name} must be a number.` };
      const unit = text(form, `unit:${a.code}`).slice(0, 20);
      payload[a.code] = unit ? { value, unit } : value;
    } else if (a.data_type === 'BOOLEAN') {
      if (value !== 'true' && value !== 'false') {
        return { ok: false, error: `${a.name} is yes or no.` };
      }
      payload[a.code] = value === 'true';
    } else {
      if (value.length > 500) return { ok: false, error: `Keep ${a.name} under 500 characters.` };
      payload[a.code] = value;
    }
  }
  return { ok: true, payload };
}

export function parseCategoryForm(form: FormData): Parsed<FormBody<Schemas['CategoryRequest']>> {
  const name = text(form, 'name');
  if (!name) return { ok: false, error: 'Give the category a name.' };
  if (name.length > 150) return { ok: false, error: 'Keep the name under 150 characters.' };
  return { ok: true, payload: { name } };
}

export function parseLinkForm(
  form: FormData
): Parsed<FormBody<Schemas['CategoryAttributeRequest']>> {
  const attribute = text(form, 'attribute');
  const role = text(form, 'role');
  if (!UUID.test(attribute)) return { ok: false, error: 'Pick an attribute.' };
  if (!ROLES.some((r) => r.value === role))
    return { ok: false, error: 'Pick how the category uses it.' };
  return {
    ok: true,
    payload: { attribute, role, is_required: form.get('is_required') === 'true' }
  };
}

export function parseAttributeForm(
  form: FormData,
  { creating }: { creating: boolean }
): Parsed<FormBody<Schemas['AttributeRequest']>> {
  const name = text(form, 'name');
  const unit = text(form, 'unit');
  if (!name) return { ok: false, error: 'Give the attribute a name.' };
  if (name.length > 150) return { ok: false, error: 'Keep the name under 150 characters.' };
  if (unit.length > 20) return { ok: false, error: 'Keep the unit under 20 characters.' };
  if (!creating) return { ok: true, payload: { name, unit } };
  const code = text(form, 'code');
  const dataType = text(form, 'data_type');
  const multi = form.get('is_multivalued') === 'true';
  if (!CODE.test(code)) {
    return { ok: false, error: 'Use lowercase letters, digits, - or _ for the code.' };
  }
  if (!DATA_TYPES.some((t) => t.value === dataType)) return { ok: false, error: 'Pick a type.' };
  if (multi && dataType !== 'OPTION') {
    return { ok: false, error: 'Only an option list takes several values.' };
  }
  return {
    ok: true,
    payload: { code, name, data_type: dataType, is_multivalued: multi, unit }
  };
}

export function parseOptionForm(
  form: FormData
): Parsed<FormBody<Schemas['AttributeOptionRequest']>> {
  const value = text(form, 'value');
  if (!value) return { ok: false, error: 'Write the option.' };
  if (value.length > 150) return { ok: false, error: 'Keep the option under 150 characters.' };
  return { ok: true, payload: { value } };
}

export const CHANNEL_KINDS = [
  { value: 'ONLINE', label: 'Online' },
  { value: 'STORE', label: 'Physical store' }
] as const;
const PHONE = /^[+\d][\d\s().-]{3,24}$/;
const STORE_LIMITS = [
  ['address_line', 255, 'address'],
  ['city', 255, 'city'],
  ['state', 255, 'state'],
  ['postcode', 64, 'postal code']
] as const;

/**
 * An online channel has a market id and no address; a physical store the
 * other way round. The form sends the other kind's fields empty, so a channel
 * switched from one kind to the other keeps nothing of the old kind.
 */
export function parseChannelForm(
  form: FormData,
  { creating }: { creating: boolean }
): Parsed<FormBody<Schemas['SalesChannelRequest']>> {
  const name = text(form, 'name');
  const kind = text(form, 'kind') || 'ONLINE';
  if (!name) return { ok: false, error: 'Give the sales channel a name.' };
  if (name.length > 100) return { ok: false, error: 'Keep the name under 100 characters.' };
  if (kind !== 'ONLINE' && kind !== 'STORE') {
    return { ok: false, error: 'Choose online or physical store.' };
  }
  const store = kind === 'STORE';
  const site = store ? '' : text(form, 'site');
  if (site.length > 50) return { ok: false, error: 'Keep the market id under 50 characters.' };
  const payload: FormBody<Schemas['SalesChannelRequest']> = {
    name,
    kind,
    site,
    is_active: form.get('is_active') !== 'false'
  };
  for (const [key, limit, label] of STORE_LIMITS) {
    const value = store ? text(form, key) : '';
    if (value.length > limit) {
      return { ok: false, error: `Keep the ${label} under ${limit} characters.` };
    }
    payload[key] = value;
  }
  const country = store ? text(form, 'country').toUpperCase() : '';
  if (country && !/^[A-Z]{2}$/.test(country)) return { ok: false, error: 'Choose a country.' };
  payload.country = country;
  const phone = store ? text(form, 'phone') : '';
  if (phone && !PHONE.test(phone)) return { ok: false, error: 'Write the phone with digits.' };
  payload.phone = phone;
  if (creating) {
    const code = text(form, 'code');
    if (!/^[a-z0-9_-]{1,50}$/.test(code)) {
      return { ok: false, error: 'Use lowercase letters, digits, - or _ for the code.' };
    }
    payload.code = code;
  }
  return { ok: true, payload };
}

// Writes --------------------------------------------------------------------

export function createProduct(
  { cookies }: Ctx,
  body: FormBody<Schemas['CatalogProductWriteRequest']>
): Promise<Schemas['CatalogProduct']> {
  return apiRequest('/catalog/products/', { method: 'POST', body }, { cookies });
}

export function updateProduct(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedCatalogProductWriteRequest']>
): Promise<Schemas['CatalogProduct']> {
  return apiRequest(`/catalog/products/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteProduct({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/products/${id}/`, { method: 'DELETE' }, { cookies });
}

export function addVariant(
  { cookies }: Ctx,
  productId: string,
  body: FormBody<Schemas['CatalogVariantWriteRequest']>
): Promise<Schemas['CatalogVariant']> {
  return apiRequest(
    `/catalog/products/${productId}/variants/`,
    { method: 'POST', body },
    { cookies }
  );
}

/** `PATCH /api/catalog/variants/<id>/`. */
export function updateVariant(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedCatalogVariantWriteRequest']>
): Promise<Schemas['CatalogVariant']> {
  return apiRequest(`/catalog/variants/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteVariant({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/variants/${id}/`, { method: 'DELETE' }, { cookies });
}

// Categories ----------------------------------------------------------------

export async function listCategories({ cookies }: Ctx) {
  const [categories, attributes] = await Promise.all([
    apiRequest<Schemas['CategoryList']>('/catalog/categories/', {}, { cookies }),
    apiRequest<Schemas['AttributeList']>('/catalog/attributes/', {}, { cookies })
  ]);
  return {
    categories: categories.results.map((c) => ({
      id: c.id,
      name: c.name,
      product_count: c.product_count,
      links: c.attributes.map((l) => ({
        id: l.id,
        attribute: l.attribute,
        code: l.code,
        name: l.name,
        role: l.role,
        is_required: l.is_required
      }))
    })),
    attributes: attributes.results.map((a) => ({ id: a.id, code: a.code, name: a.name })),
    can_manage: viewerIsAdmin(cookies)
  };
}

export function createCategory(
  { cookies }: Ctx,
  body: FormBody<Schemas['CategoryRequest']>
): Promise<Schemas['Category']> {
  return apiRequest('/catalog/categories/', { method: 'POST', body }, { cookies });
}

export function renameCategory(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedCategoryRequest']>
): Promise<Schemas['Category']> {
  return apiRequest(`/catalog/categories/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteCategory({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/categories/${id}/`, { method: 'DELETE' }, { cookies });
}

export function linkAttribute(
  { cookies }: Ctx,
  categoryId: string,
  body: FormBody<Schemas['CategoryAttributeRequest']>
): Promise<Schemas['CategoryAttribute']> {
  return apiRequest(
    `/catalog/categories/${categoryId}/attributes/`,
    { method: 'POST', body },
    { cookies }
  );
}

export function unlinkAttribute({ cookies }: Ctx, linkId: string): Promise<unknown> {
  return apiRequest(`/catalog/category-attributes/${linkId}/`, { method: 'DELETE' }, { cookies });
}

// Attributes ----------------------------------------------------------------

export async function listAttributes({ cookies }: Ctx) {
  const attributes = await apiRequest<Schemas['AttributeList']>(
    '/catalog/attributes/',
    {},
    { cookies }
  );
  return {
    attributes: attributes.results.map((a) => ({
      ...toAttribute(a),
      channel_codes: a.channel_codes.map((c) => `${c.channel_code}: ${c.external_code}`)
    })),
    can_manage: viewerIsAdmin(cookies)
  };
}

export function createAttribute(
  { cookies }: Ctx,
  body: FormBody<Schemas['AttributeRequest']>
): Promise<Schemas['Attribute']> {
  return apiRequest('/catalog/attributes/', { method: 'POST', body }, { cookies });
}

export function updateAttribute(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedAttributeRequest']>
): Promise<Schemas['Attribute']> {
  return apiRequest(`/catalog/attributes/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteAttribute({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/attributes/${id}/`, { method: 'DELETE' }, { cookies });
}

export function addOption(
  { cookies }: Ctx,
  attributeId: string,
  body: FormBody<Schemas['AttributeOptionRequest']>
): Promise<Schemas['AttributeOption']> {
  return apiRequest(
    `/catalog/attributes/${attributeId}/options/`,
    { method: 'POST', body },
    { cookies }
  );
}

export function renameOption(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedAttributeOptionRequest']>
): Promise<Schemas['AttributeOption']> {
  return apiRequest(`/catalog/attribute-options/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteOption({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/attribute-options/${id}/`, { method: 'DELETE' }, { cookies });
}

// Sales channels ------------------------------------------------------------

export async function listChannels({ cookies }: Ctx) {
  const channels = await apiRequest<Schemas['SalesChannelList']>(
    '/catalog/channels/',
    {},
    { cookies }
  );
  return {
    channels: channels.results.map((c) => ({
      id: c.id,
      name: c.name,
      code: c.code,
      kind: c.kind ?? 'ONLINE',
      site: c.site ?? '',
      address_line: c.address_line ?? '',
      city: c.city ?? '',
      state: c.state ?? '',
      postcode: c.postcode ?? '',
      country: c.country ?? '',
      phone: c.phone ?? '',
      is_active: c.is_active ?? true
    })),
    can_manage: viewerIsAdmin(cookies)
  };
}

export function createChannel(
  { cookies }: Ctx,
  body: FormBody<Schemas['SalesChannelRequest']>
): Promise<Schemas['SalesChannel']> {
  return apiRequest('/catalog/channels/', { method: 'POST', body }, { cookies });
}

export function updateChannel(
  { cookies }: Ctx,
  id: string,
  body: FormBody<Schemas['PatchedSalesChannelRequest']>
): Promise<Schemas['SalesChannel']> {
  return apiRequest(`/catalog/channels/${id}/`, { method: 'PATCH', body }, { cookies });
}

export function deleteChannel({ cookies }: Ctx, id: string): Promise<unknown> {
  return apiRequest(`/catalog/channels/${id}/`, { method: 'DELETE' }, { cookies });
}

// Importing a channel's report ------------------------------------------------

export type ImportReport = Schemas['ImportReport'];
type ImportRun = Schemas['ImportRun'];
export const MAX_IMPORT_MB = 20;
const MAX_IMPORT_BYTES = MAX_IMPORT_MB * 1024 * 1024;

/**
 * What can be imported: the channels with an importer (Mercado Libre), and
 * every other channel, offered but not available until its report has been
 * analyzed and an importer written for it. Admins only.
 */
export async function listImportSources({ cookies }: Ctx) {
  const reply = await apiRequest<Schemas['ImportSourceList']>(
    '/catalog/import/sources/',
    {},
    { cookies }
  );
  return reply.results.map((s) => ({
    code: s.code,
    name: s.name,
    available: s.available,
    reason: s.reason,
    files: s.files.map((f) => ({ field: f.field, label: f.label })),
    kind: s.channel?.kind ?? 'ONLINE'
  }));
}

export type ImportSource = Awaited<ReturnType<typeof listImportSources>>[number];

function toRun(run: ImportRun) {
  return {
    id: run.id,
    source: run.source_name,
    dry_run: run.dry_run,
    status: run.status,
    notify: run.notify,
    files: Object.values(run.file_names ?? {}),
    files_kept: run.files_kept,
    started_by: run.started_by,
    error: run.error,
    created_at: run.created_at,
    finished_at: run.finished_at,
    emailed_at: run.emailed_at
  };
}

export async function listImportRuns({ cookies }: Ctx, limit = 10) {
  const page = await apiRequest<Schemas['ImportRunPage']>(
    `/catalog/import/runs/?limit=${limit}`,
    {},
    { cookies }
  );
  return page.results.map(toRun);
}

/** One run, its report shaped for the page once it has one. */
export async function getImportRun({ cookies }: Ctx, id: string) {
  const run = await apiRequest<Schemas['ImportRunDetail']>(
    `/catalog/import/runs/${id}/`,
    {},
    { cookies }
  );
  return { ...toRun(run), report: run.report ? shapeReport(run.report) : null };
}

/**
 * A source's files from a submitted form, with the preview or import choice
 * and whether to email the report, or what is wrong. The web's check (every
 * file there, .xlsx, 20 MB); the API checks them again.
 */
export function parseImportForm(form: FormData, source: ImportSource): Parsed<FormData> {
  if (!source.available) return { ok: false, error: source.reason };
  const body = new FormData();
  body.set('source', source.code);
  body.set('dry_run', form.get('mode') === 'import' ? 'false' : 'true');
  body.set('notify', form.get('notify') === 'on' ? 'true' : 'false');
  for (const { field } of source.files) {
    const file = form.get(field);
    if (!(file instanceof File) || file.size === 0) {
      return { ok: false, error: `Choose the ${field} file.` };
    }
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      return { ok: false, error: `The ${field} file must be an .xlsx workbook.` };
    }
    if (file.size > MAX_IMPORT_BYTES) {
      return {
        ok: false,
        error: `The ${field} file exceeds the ${MAX_IMPORT_MB} MB limit.`
      };
    }
    body.set(field, file, file.name);
  }
  return { ok: true, payload: body };
}

/** Store the files and queue the run; it answers at once with the queued run. */
export function startImport({ cookies }: Ctx, body: FormData): Promise<Schemas['ImportRunDetail']> {
  return apiRequest('/catalog/import/runs/', { method: 'POST', body }, { cookies });
}

/** Import the files a finished preview checked, without sending them again. */
export function importPreviewed(
  { cookies }: Ctx,
  id: string,
  notify: boolean
): Promise<Schemas['ImportRunDetail']> {
  return apiRequest(
    `/catalog/import/runs/${id}/import/`,
    { method: 'POST', body: { notify } },
    { cookies }
  );
}

const COUNT_LABELS: Record<string, string> = {
  channels: 'Sales channels',
  categories: 'Categories',
  attributes: 'Attributes',
  category_attributes: 'Attributes linked to categories',
  channel_attributes: "Mercado Libre's attribute codes",
  products: 'Products',
  variants: 'Variants',
  listings: 'Listings',
  listing_variants: 'Variants published in listings'
};
const NOTE_LABELS: Record<string, string> = {
  duplicate_publications_ignored: 'Duplicate publications ignored',
  designs_recovered_from_label: "Designs read from Mercado Libre's variant labels",
  sizes_recovered_from_label: "Sizes read from Mercado Libre's variant labels"
};

/** The report as the page shows it: counts in a fixed order, sentences for the rest. */
export function shapeReport(report: ImportReport) {
  const kinds = [
    ...Object.keys(COUNT_LABELS),
    ...Object.keys({ ...report.created, ...report.updated }).filter((k) => !(k in COUNT_LABELS))
  ];
  return {
    counts: kinds
      .map((kind) => ({
        label: COUNT_LABELS[kind] ?? kind.replaceAll('_', ' '),
        created: report.created[kind] ?? 0,
        updated: report.updated[kind] ?? 0
      }))
      .filter((row) => row.created || row.updated),
    notes: Object.entries(report.notes).map(([key, n]) => ({
      label: NOTE_LABELS[key] ?? key.replaceAll('_', ' '),
      count: n
    })),
    empty_categories: report.empty_categories.map((c) =>
      c.rows
        ? `${c.category}: no products, all ${c.rows} of its rows were rejected.`
        : `${c.category}: no products, its sheet has no rows.`
    ),
    rejected: report.rejected,
    warnings: report.warnings
  };
}
