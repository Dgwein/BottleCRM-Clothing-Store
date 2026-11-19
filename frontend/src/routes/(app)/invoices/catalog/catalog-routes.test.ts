/**
 * The catalog pages, driven the way the browser drives them: each page's
 * load (what it asks the API for and what it hands the page to show) and
 * each form action (what it refuses before sending, what it sends, and what
 * the page is told when the API refuses). R-cases in the catalog test plan.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type Call = { path: string; method: string; body: unknown };
let calls: Call[] = [];
let respond: (call: Call) => unknown = () => ({});
let admin = true;

vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: async (path: string, options: { method?: string; body?: unknown } = {}) => {
    const call = { path, method: options.method ?? 'GET', body: options.body };
    calls.push(call);
    return respond(call);
  }
}));
vi.mock('#lib/server/v2/organization.js', () => ({
  viewerIsAdmin: () => admin,
  getNumbering: async () => ({})
}));

const list = await import('./+page.server.js');
const created = await import('./new/+page.server.js');
const detail = await import('./[id]/+page.server.js');
const categories = await import('./categories/+page.server.js');
const attributes = await import('./attributes/+page.server.js');
const channels = await import('./channels/+page.server.js');
const importing = await import('./import/+page.server.js');
const importRun = await import('./import/[id]/+page.server.js');
const newInvoice = await import('../new/+page.server.js');
const { getInvoice } = await import('#lib/server/v2/invoices.js');

const CAT = '3f2b8f9e-2c1d-4a5b-9c8d-1e2f3a4b5c6d';
const ATTR = '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d';

function formOf(fields: Record<string, string | string[]>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    for (const v of Array.isArray(value) ? value : [value]) form.append(key, v);
  }
  return form;
}

function event(
  fields: Record<string, string | string[]> = {},
  params = {},
  url = 'https://app.test/x'
) {
  return {
    request: new Request(url, { method: 'POST', body: formOf(fields) }),
    params,
    url: new URL(url),
    cookies: { get: () => undefined }
  } as never;
}

const refuse = (status: number, body: Record<string, unknown>) => {
  throw Object.assign(new Error(`HTTP ${status}`), { status, body });
};

const writes = () => calls.filter((c) => c.method !== 'GET');

/** A load's data; route loads are typed as possibly void. */
const loaded = async (result: unknown): Promise<any> => {
  const data = await result;
  if (!data) throw new Error('The load returned nothing');
  return data;
};

beforeEach(() => {
  calls = [];
  respond = () => ({});
  admin = true;
});

// Fixtures in the API's shapes ------------------------------------------------

const productRow = (n: number) => ({
  id: `p${n}`,
  name: `Vestido ${n}`,
  category: { id: CAT, name: 'Vestidos' },
  is_active: true,
  variant_count: 2,
  active_variant_count: 2,
  stock: 7,
  sizes: ['RN', '0-3 meses'],
  listing_count: 1,
  price_min: '499.00',
  price_max: '549.00',
  currency: 'MXN',
  created_at: ''
});

const option = (value: string, i = 0) => ({ id: `o-${value}`, value, aliases: [], sort_order: i });
const ATTRIBUTES = {
  results: [
    {
      id: ATTR,
      code: 'color',
      name: 'Color',
      data_type: 'OPTION',
      is_multivalued: false,
      unit: '',
      options: [option('Rojo'), option('Azul', 1)],
      channel_codes: []
    },
    {
      id: 'a-material',
      code: 'material',
      name: 'Material',
      data_type: 'OPTION',
      is_multivalued: false,
      unit: '',
      options: [option('Algodón')],
      channel_codes: [
        {
          id: 'm1',
          channel: 'ch1',
          channel_code: 'mercadolibre',
          external_code: 'MAIN_MATERIAL',
          external_domain: ''
        }
      ]
    }
  ]
};
const CATEGORIES = {
  results: [
    {
      id: CAT,
      name: 'Vestidos',
      parent: null,
      product_count: 1,
      attributes: [
        {
          id: 'l1',
          attribute: ATTR,
          code: 'color',
          name: 'Color',
          data_type: 'OPTION',
          role: 'VARIANT_AXIS',
          is_required: true,
          sort_order: 0
        },
        {
          id: 'l2',
          attribute: 'a-material',
          code: 'material',
          name: 'Material',
          data_type: 'OPTION',
          role: 'CHARACTERISTIC',
          is_required: false,
          sort_order: 1
        }
      ]
    }
  ]
};
const PRODUCT = {
  id: 'p1',
  name: 'Vestido',
  description: 'De fiesta.',
  category: { id: CAT, name: 'Vestidos' },
  is_active: true,
  sizes: ['RN'],
  axes: [{ code: 'color', name: 'Color', is_required: true, options: ['Rojo'] }],
  attributes: [
    { code: 'material', name: 'Material', data_type: 'OPTION', value: 'Algodón', unit: '' }
  ],
  variants: [
    {
      id: 'v1',
      product: 'p1',
      name: 'Vestido (RN / Rojo)',
      size: 'RN',
      stock: 5,
      price: '499.00',
      currency: 'MXN',
      sku: null,
      gtin: '',
      is_active: true,
      axes: { color: 'Rojo' },
      attributes: [],
      package_height_cm: null,
      package_width_cm: null,
      package_depth_cm: null,
      package_weight_kg: null,
      listings: []
    }
  ],
  listings: [],
  created_at: '',
  updated_at: ''
};

function catalogApi(overrides: (call: Call) => unknown = () => undefined) {
  respond = (call) => {
    const answer = overrides(call);
    if (answer !== undefined) return answer;
    if (call.path === '/catalog/products/p1/' && call.method === 'GET') return PRODUCT;
    if (call.path === '/catalog/categories/') return CATEGORIES;
    if (call.path === '/catalog/attributes/') return ATTRIBUTES;
    if (call.path === '/catalog/sizes/') return { results: [{ size: 'RN', variant_count: 1 }] };
    if (call.path === '/catalog/channels/') {
      return {
        results: [
          {
            id: 'ch1',
            name: 'Mercado Libre',
            code: 'mercadolibre',
            kind: 'ONLINE',
            site: 'MLM',
            address_line: '',
            city: '',
            state: '',
            postcode: '',
            country: '',
            phone: '',
            is_active: true,
            created_at: ''
          },
          {
            id: 'ch2',
            name: 'Tienda Centro',
            code: 'centro',
            kind: 'STORE',
            site: '',
            address_line: 'Av. Juárez 12',
            city: 'Puebla',
            state: 'Puebla',
            postcode: '72000',
            country: 'MX',
            phone: '+52 222 123 4567',
            is_active: true,
            created_at: ''
          },
          {
            id: 'ch3',
            name: 'Amazon',
            code: 'amazon',
            kind: 'ONLINE',
            site: '',
            is_active: false,
            created_at: ''
          }
        ]
      };
    }
    return {};
  };
}

// R1 List, query and paging ----------------------------------------------------

describe('R1 the product list', () => {
  it('(+) asks for one filtered page and shows the range and the page links', async () => {
    catalogApi((call) =>
      call.path.startsWith('/catalog/products/?')
        ? { count: 120, next: null, previous: null, results: [productRow(51), productRow(52)] }
        : undefined
    );
    const data = await loaded(
      list.load(
        event(
          {},
          {},
          `https://app.test/invoices/catalog?q=vestido&size=2A&in_stock=true&category=${CAT}&offset=50`
        )
      )
    );
    const asked = new URL(`https://x${calls[0].path}`).searchParams;
    expect(Object.fromEntries(asked)).toEqual({
      limit: '50',
      offset: '50',
      search: 'vestido',
      category: CAT,
      size: '2A',
      in_stock: 'true'
    });
    expect(data).toMatchObject({ count: 120, from: 51, to: 52, can_manage: true });
    expect(data.prev).toBe(`?q=vestido&category=${CAT}&size=2A&in_stock=true`);
    expect(data.next).toBe(`?q=vestido&category=${CAT}&size=2A&in_stock=true&offset=100`);
    expect(data.products[0]).toMatchObject({
      name: 'Vestido 51',
      sizes: ['RN', '0-3 meses'],
      stock: 7,
      price_min: 499
    });
    expect(data.sizes).toEqual(['RN']);
    expect(data.categories).toEqual([{ id: CAT, name: 'Vestidos', product_count: 1 }]);
  });

  it('(+) the first page has no previous link and the last no next link', async () => {
    catalogApi((call) =>
      call.path.startsWith('/catalog/products/?')
        ? { count: 2, next: null, previous: null, results: [productRow(1), productRow(2)] }
        : undefined
    );
    const data = await loaded(list.load(event({}, {}, 'https://app.test/invoices/catalog')));
    expect([data.prev, data.next, data.from, data.to]).toEqual([null, null, 1, 2]);
  });

  it('(-) an empty result shows 0 to 0 and a member gets no write buttons', async () => {
    admin = false;
    catalogApi((call) =>
      call.path.startsWith('/catalog/products/?')
        ? { count: 0, next: null, previous: null, results: [] }
        : undefined
    );
    const data = await loaded(
      list.load(event({}, {}, 'https://app.test/invoices/catalog?size=Nada'))
    );
    expect(data).toMatchObject({ count: 0, from: 0, to: 0, can_manage: false, products: [] });
  });
});

// R2 New product ----------------------------------------------------------------

describe('R2 a new product', () => {
  it('(-) a member is turned away from the form', async () => {
    admin = false;
    catalogApi();
    await expect(created.load(event())).rejects.toMatchObject({ status: 403 });
  });

  it('(+) an admin gets the categories to pick from', async () => {
    catalogApi();
    expect(await created.load(event())).toEqual({ categories: [{ id: CAT, name: 'Vestidos' }] });
  });

  it('(-) a form without a name or category is refused before anything is sent', async () => {
    const refused = await created.actions.create(event({ name: '', category: CAT }));
    expect(refused).toMatchObject({ status: 400, data: { error: 'Give the product a name.' } });
    const noCategory = await created.actions.create(event({ name: 'Vestido', category: '' }));
    expect(noCategory).toMatchObject({ status: 400 });
    expect(writes()).toEqual([]);
  });

  it('(+) a good form creates the product and opens its page', async () => {
    catalogApi((call) => (call.method === 'POST' ? { ...PRODUCT, id: 'p9' } : undefined));
    await expect(
      created.actions.create(event({ name: 'Vestido', category: CAT, description: 'X' }))
    ).rejects.toMatchObject({ status: 303, location: '/invoices/catalog/p9' });
    expect(writes()).toEqual([
      {
        path: '/catalog/products/',
        method: 'POST',
        body: { name: 'Vestido', description: 'X', category: CAT }
      }
    ]);
  });

  it("(-) the API's refusal is shown beside the form", async () => {
    catalogApi((call) =>
      call.method === 'POST' ? refuse(400, { name: ['Too long.'] }) : undefined
    );
    const refused = await created.actions.create(event({ name: 'Vestido', category: CAT }));
    expect(refused).toMatchObject({ status: 400, data: { form: 'create' } });
  });
});

// R3 Product page -----------------------------------------------------------------

describe('R3 the product page', () => {
  it("(+) shows the product with its category's characteristics and axes", async () => {
    catalogApi();
    const data = await loaded(detail.load(event({}, { id: 'p1' })));
    expect(data.characteristics).toEqual([
      expect.objectContaining({
        code: 'material',
        value: 'Algodón',
        options: [expect.objectContaining({ value: 'Algodón' })]
      })
    ]);
    expect(data.axis_fields).toEqual([
      expect.objectContaining({ code: 'color', is_required: true })
    ]);
    expect(data.variants[0]).toMatchObject({ size: 'RN', stock: 5, price: 499 });
    expect(data.product.attributes).toEqual([
      { code: 'material', name: 'Material', text: 'Algodón' }
    ]);
  });

  it('(-) a product that does not exist is a 404 page', async () => {
    catalogApi((call) => (call.path === '/catalog/products/nope/' ? refuse(404, {}) : undefined));
    await expect(detail.load(event({}, { id: 'nope' }))).rejects.toMatchObject({ status: 404 });
  });

  it('(+) saves characteristics, an empty one removed', async () => {
    catalogApi();
    const result = await detail.actions.characteristics(
      event({ 'attr:material': '' }, { id: 'p1' })
    );
    expect(result).toEqual({ form: 'characteristics', saved: true });
    expect(writes()).toEqual([
      { path: '/catalog/products/p1/', method: 'PATCH', body: { attributes: { material: null } } }
    ]);
  });

  it('(-) an option the attribute does not have is refused before sending', async () => {
    catalogApi();
    const refused = await detail.actions.characteristics(
      event({ 'attr:material': 'Lino' }, { id: 'p1' })
    );
    expect(refused).toMatchObject({ status: 400, data: { form: 'characteristics' } });
    expect(writes()).toEqual([]);
  });

  it('(+) adds a variant with its size, stock, price and axes', async () => {
    catalogApi();
    const result = await detail.actions.addVariant(
      event({ size: '2A', price: '599.00', stock: '3', 'axis:color': 'Azul' }, { id: 'p1' })
    );
    expect(result).toEqual({ form: 'addVariant', saved: true });
    expect(writes()).toEqual([
      {
        path: '/catalog/products/p1/variants/',
        method: 'POST',
        body: { price: '599.00', stock: 3, size: '2A', sku: null, attributes: { color: 'Azul' } }
      }
    ]);
  });

  it('(-) a variant without a price or its required color is refused before sending', async () => {
    catalogApi();
    const cases: Record<string, string>[] = [{ size: '2A', 'axis:color': 'Azul' }, { price: '10' }];
    for (const fields of cases) {
      const refused = await detail.actions.addVariant(event(fields, { id: 'p1' }));
      expect(refused).toMatchObject({ status: 400, data: { form: 'addVariant' } });
    }
    expect(writes()).toEqual([]);
  });

  it("(-) the API's duplicate-variant refusal is shown on the form", async () => {
    catalogApi((call) =>
      call.method === 'POST'
        ? refuse(400, {
            attributes: ['Another variant of this product has this size and these values.']
          })
        : undefined
    );
    const refused = await detail.actions.addVariant(
      event({ size: 'RN', price: '1', 'axis:color': 'Rojo' }, { id: 'p1' })
    );
    expect(refused).toMatchObject({ status: 400, data: { form: 'addVariant' } });
    expect(String((refused as { data: { error: string } }).data.error)).toContain(
      'Another variant'
    );
  });

  it('(+) saves a variant row; (-) a price of 0 is refused before sending', async () => {
    catalogApi();
    const saved = await detail.actions.variant(
      event({ id: 'v1', price: '459.00', stock: '2', size: 'RN', is_active: 'true' }, { id: 'p1' })
    );
    expect(saved).toEqual({ form: 'variant:v1', saved: true });
    const refused = await detail.actions.variant(
      event({ id: 'v1', price: '0', stock: '2', is_active: 'true' }, { id: 'p1' })
    );
    expect(refused).toMatchObject({ status: 400, data: { form: 'variant:v1' } });
    expect(writes()).toEqual([
      {
        path: '/catalog/variants/v1/',
        method: 'PATCH',
        body: { price: '459.00', stock: 2, size: 'RN', is_active: true }
      }
    ]);
  });

  it('(-) a variant or product that is sold is not deleted, and the page says why', async () => {
    const why = 'Vestido (RN / Rojo) is on 1 invoice, so it cannot be deleted. Retire it instead.';
    catalogApi((call) => (call.method === 'DELETE' ? refuse(409, { detail: why }) : undefined));
    const variant = await detail.actions.deleteVariant(event({ id: 'v1' }, { id: 'p1' }));
    expect(variant).toMatchObject({ status: 409, data: { form: 'variant:v1', error: why } });
    const product = await detail.actions.delete(event({}, { id: 'p1' }));
    expect(product).toMatchObject({ status: 409, data: { form: 'delete', error: why } });
  });

  it('(+) deleting an unsold product returns to the list', async () => {
    catalogApi();
    await expect(detail.actions.delete(event({}, { id: 'p1' }))).rejects.toMatchObject({
      status: 303,
      location: '/invoices/catalog'
    });
  });

  it('(-) a member editing is told only an administrator can', async () => {
    catalogApi((call) => (call.method === 'PATCH' ? refuse(403, { detail: 'no' }) : undefined));
    const refused = await detail.actions.product(
      event({ name: 'Vestido', is_active: 'true' }, { id: 'p1' })
    );
    expect(refused).toMatchObject({
      status: 403,
      data: { error: 'Only an administrator can change the catalog.' }
    });
  });
});

// R4 Categories ---------------------------------------------------------------------

describe('R4 categories', () => {
  it('(+) lists categories with their counts and attributes', async () => {
    catalogApi();
    const data = await loaded(categories.load(event()));
    expect(data.categories[0]).toMatchObject({
      name: 'Vestidos',
      product_count: 1,
      links: [
        expect.objectContaining({ name: 'Color', role: 'VARIANT_AXIS' }),
        expect.objectContaining({ name: 'Material' })
      ]
    });
    expect(data.attributes.map((a: { code: string }) => a.code)).toEqual(['color', 'material']);
  });

  it('(+) creates, renames, links and unlinks', async () => {
    catalogApi();
    await categories.actions.create(event({ name: 'Pijamas' }));
    await categories.actions.rename(event({ id: CAT, name: 'Vestidos bebé' }));
    await categories.actions.link(
      event({ id: CAT, attribute: ATTR, role: 'CHARACTERISTIC', is_required: 'false' })
    );
    await categories.actions.unlink(event({ id: 'l2', category: CAT }));
    expect(writes().map((c) => [c.method, c.path])).toEqual([
      ['POST', '/catalog/categories/'],
      ['PATCH', `/catalog/categories/${CAT}/`],
      ['POST', `/catalog/categories/${CAT}/attributes/`],
      ['DELETE', '/catalog/category-attributes/l2/']
    ]);
  });

  it('(-) a blank name is refused; a category in use or with fixed axes says why', async () => {
    const blank = await categories.actions.create(event({ name: '  ' }));
    expect(blank).toMatchObject({ status: 400, data: { form: 'create' } });
    catalogApi((call) => {
      if (call.method === 'DELETE')
        return refuse(409, { detail: 'Products or other categories still use this category.' });
      if (call.method === 'POST')
        return refuse(400, {
          role: ['Vestidos already has variants, so its variant axes are fixed.']
        });
      return undefined;
    });
    const inUse = await categories.actions.delete(event({ id: CAT }));
    expect(inUse).toMatchObject({ status: 409, data: { form: `category:${CAT}` } });
    const fixed = await categories.actions.link(
      event({ id: CAT, attribute: ATTR, role: 'VARIANT_AXIS' })
    );
    expect(fixed).toMatchObject({ status: 400, data: { form: `category:${CAT}` } });
  });
});

// R5 Attributes -----------------------------------------------------------------------

describe('R5 attributes', () => {
  it('(+) lists attributes with their options and channel columns', async () => {
    catalogApi();
    const data = await loaded(attributes.load(event()));
    expect(data.attributes[1]).toMatchObject({
      code: 'material',
      options: [expect.objectContaining({ value: 'Algodón' })],
      channel_codes: ['mercadolibre: MAIN_MATERIAL']
    });
  });

  it('(+) creates an attribute and manages its options', async () => {
    catalogApi();
    await attributes.actions.create(
      event({ code: 'season', name: 'Temporada', data_type: 'OPTION', is_multivalued: 'true' })
    );
    await attributes.actions.update(event({ id: ATTR, name: 'Color principal', unit: '' }));
    await attributes.actions.addOption(event({ id: ATTR, value: 'Verde' }));
    await attributes.actions.renameOption(
      event({ id: 'o-Rojo', attribute: ATTR, value: 'Rojo carmín' })
    );
    await attributes.actions.deleteOption(event({ id: 'o-Azul', attribute: ATTR }));
    expect(writes().map((c) => [c.method, c.path])).toEqual([
      ['POST', '/catalog/attributes/'],
      ['PATCH', `/catalog/attributes/${ATTR}/`],
      ['POST', `/catalog/attributes/${ATTR}/options/`],
      ['PATCH', '/catalog/attribute-options/o-Rojo/'],
      ['DELETE', '/catalog/attribute-options/o-Azul/']
    ]);
  });

  it('(-) a bad code or blank option is refused before sending; one in use says why', async () => {
    const code = await attributes.actions.create(
      event({ code: 'Bad Code', name: 'X', data_type: 'TEXT' })
    );
    expect(code).toMatchObject({ status: 400 });
    const blank = await attributes.actions.addOption(event({ id: ATTR, value: '' }));
    expect(blank).toMatchObject({ status: 400, data: { form: `attribute:${ATTR}` } });
    expect(writes()).toEqual([]);
    catalogApi((call) =>
      call.method === 'DELETE'
        ? refuse(409, { detail: 'Products still use this option.' })
        : undefined
    );
    const used = await attributes.actions.deleteOption(event({ id: 'o-Rojo', attribute: ATTR }));
    expect(used).toMatchObject({ status: 409, data: { error: 'Products still use this option.' } });
  });
});

// R6 Sales channels ---------------------------------------------------------------------

const ONLINE_EMPTY = {
  site: '',
  address_line: '',
  city: '',
  state: '',
  postcode: '',
  country: '',
  phone: ''
};

describe('R6 sales channels', () => {
  it('(+) lists online channels and physical stores with their kind and address', async () => {
    catalogApi();
    const data = await loaded(channels.load(event()));
    type Row = { name: string; kind: string; city: string; is_active: boolean };
    expect(data.channels.map((c: Row) => [c.name, c.kind, c.city, c.is_active])).toEqual([
      ['Mercado Libre', 'ONLINE', '', true],
      ['Tienda Centro', 'STORE', 'Puebla', true],
      ['Amazon', 'ONLINE', '', false]
    ]);
    expect(data.kinds.map((k: { value: string }) => k.value)).toEqual(['ONLINE', 'STORE']);
  });

  it('(+) creates a physical store and edits an online channel', async () => {
    catalogApi();
    await channels.actions.create(
      event({
        name: 'Tienda Centro',
        code: 'centro',
        kind: 'STORE',
        address_line: 'Av. Juárez 12',
        city: 'Puebla',
        country: 'MX'
      })
    );
    await channels.actions.update(
      event({
        id: 'ch1',
        name: 'Mercado Libre MX',
        kind: 'ONLINE',
        site: 'MLM',
        is_active: 'false'
      })
    );
    expect(writes()).toEqual([
      {
        path: '/catalog/channels/',
        method: 'POST',
        body: {
          name: 'Tienda Centro',
          kind: 'STORE',
          ...ONLINE_EMPTY,
          address_line: 'Av. Juárez 12',
          city: 'Puebla',
          country: 'MX',
          is_active: true,
          code: 'centro'
        }
      },
      {
        path: '/catalog/channels/ch1/',
        method: 'PATCH',
        body: {
          name: 'Mercado Libre MX',
          kind: 'ONLINE',
          ...ONLINE_EMPTY,
          site: 'MLM',
          is_active: false
        }
      }
    ]);
  });

  it('(-) a bad code is refused before sending; a channel with sales says why', async () => {
    const bad = await channels.actions.create(event({ name: 'Amazon', code: 'Amazon MX' }));
    expect(bad).toMatchObject({ status: 400, data: { form: 'create' } });
    expect(writes()).toEqual([]);
    const refusal =
      'Mercado Libre has 784 listings and 3 invoices, so it cannot be deleted. Switch it off instead.';
    catalogApi((call) => (call.method === 'DELETE' ? refuse(409, { detail: refusal }) : undefined));
    const inUse = await channels.actions.delete(event({ id: 'ch1' }));
    expect(inUse).toMatchObject({ status: 409, data: { form: 'channel:ch1', error: refusal } });
  });

  it('(-) the API refusing a store with a market id is shown beside the form', async () => {
    catalogApi((call) =>
      call.method === 'PATCH'
        ? refuse(400, {
            kind: ['This channel has listings or attribute codes, so it stays online.']
          })
        : undefined
    );
    const refused = await channels.actions.update(event({ id: 'ch1', name: 'ML', kind: 'STORE' }));
    expect(refused).toMatchObject({
      status: 400,
      data: {
        form: 'channel:ch1',
        error: 'This channel has listings or attribute codes, so it stays online.'
      }
    });
  });
});

// R7 Importing a report ---------------------------------------------------------------------

function upload(fields: Record<string, string>, files: Record<string, File>) {
  const body = formOf(fields);
  for (const [key, file] of Object.entries(files)) body.set(key, file);
  return {
    request: new Request('https://app.test/invoices/catalog/import', { method: 'POST', body }),
    params: { id: 'r1' },
    url: new URL('https://app.test/invoices/catalog/import'),
    cookies: { get: () => undefined }
  } as never;
}

const SOURCES = {
  results: [
    {
      code: 'mercadolibre',
      name: 'Mercado Libre',
      available: true,
      reason: '',
      files: [
        { field: 'characteristics', label: 'Characteristics' },
        { field: 'prices', label: 'Prices' }
      ],
      channel: { id: 'ch1', name: 'Mercado Libre', kind: 'ONLINE' }
    },
    {
      code: 'centro',
      name: 'Tienda Centro',
      available: false,
      reason: 'Importing from Tienda Centro is not available yet.',
      files: [],
      channel: { id: 'ch2', name: 'Tienda Centro', kind: 'STORE' }
    }
  ]
};
const RUN = {
  id: 'r1',
  source: 'mercadolibre',
  source_name: 'Mercado Libre',
  dry_run: true,
  status: 'DONE',
  notify: false,
  file_names: { characteristics: 'characteristics.xlsx', prices: 'prices.xlsx' },
  files_kept: true,
  started_by: 'Camille',
  error: '',
  created_at: '2026-11-18T10:00:00Z',
  started_at: '2026-11-18T10:00:01Z',
  finished_at: '2026-11-18T10:00:31Z',
  emailed_at: null,
  report: {
    created: { products: 89, variants: 939 },
    updated: {},
    notes: {},
    empty_categories: [
      { category: 'Sudaderas y hoodies', sheet: 'Sudaderas y hoodies', rows: 15, rejected: 15 }
    ],
    warnings: [],
    rejected: ['Vestidos, row 9: publication MLM1, product U1 was not loaded: it has no price.']
  }
};
const BOTH = () => ({
  characteristics: new File([new Uint8Array(8)], 'characteristics.xlsx'),
  prices: new File([new Uint8Array(8)], 'prices.xlsx')
});
const importApi = (overrides: (call: Call) => unknown = () => undefined) =>
  catalogApi((call) => {
    const answer = overrides(call);
    if (answer !== undefined) return answer;
    if (call.path === '/catalog/import/sources/') return SOURCES;
    if (call.path.startsWith('/catalog/import/runs/?')) {
      return { count: 1, next: null, previous: null, results: [RUN] };
    }
    if (call.path.startsWith('/catalog/import/runs/')) return { ...RUN, id: 'r2' };
    return undefined;
  });

async function redirectOf(work: unknown) {
  try {
    await work;
  } catch (thrown) {
    return thrown as { status: number; location: string };
  }
  throw new Error('expected a redirect');
}

describe("R7 importing a channel's report", () => {
  it('(+) an admin sees every source, the unavailable ones with why, and recent runs', async () => {
    importApi();
    const data = await loaded(importing.load(event()));
    expect(
      data.sources.map((s: { name: string; available: boolean }) => [s.name, s.available])
    ).toEqual([
      ['Mercado Libre', true],
      ['Tienda Centro', false]
    ]);
    expect(data.sources[1].reason).toContain('not available yet');
    expect(data.runs.map((r: { id: string; status: string }) => [r.id, r.status])).toEqual([
      ['r1', 'DONE']
    ]);
  });

  it('(-) a member is told only an administrator can import, and nothing is asked', async () => {
    admin = false;
    importApi();
    const data = await loaded(importing.load(event()));
    expect([data.can_manage, data.sources, data.runs]).toEqual([false, [], []]);
    expect(calls).toEqual([]);
  });

  it('(+) Preview sends both files as a dry run and opens the queued run', async () => {
    importApi();
    const moved = await redirectOf(
      importing.actions.start(upload({ source: 'mercadolibre', mode: 'preview' }, BOTH()))
    );
    expect(moved).toMatchObject({ status: 303, location: '/invoices/catalog/import/r2' });
    const sent = writes()[0];
    expect(sent.path).toBe('/catalog/import/runs/');
    const body = sent.body as FormData;
    expect([body.get('source'), body.get('dry_run'), body.get('notify')]).toEqual([
      'mercadolibre',
      'true',
      'false'
    ]);
    expect([...body.keys()].sort()).toEqual([
      'characteristics',
      'dry_run',
      'notify',
      'prices',
      'source'
    ]);
  });

  it('(-) a missing file, or a source with no importer, is refused before sending', async () => {
    importApi();
    const { prices } = BOTH();
    const missing = await importing.actions.start(
      upload({ source: 'mercadolibre', mode: 'import' }, { prices })
    );
    expect(missing).toMatchObject({
      status: 400,
      data: { source: 'mercadolibre', error: 'Choose the characteristics file.' }
    });
    const store = await importing.actions.start(
      upload({ source: 'centro', mode: 'import' }, BOTH())
    );
    expect(store).toMatchObject({
      status: 400,
      data: { error: 'Importing from Tienda Centro is not available yet.' }
    });
    expect(writes()).toEqual([]);
  });

  it("(-) the API's refusals are shown: a bad file, a run in progress, too many tries", async () => {
    const cases: [number, Record<string, unknown>, string][] = [
      [
        400,
        { prices: ['The prices file is not an .xlsx workbook.'] },
        'The prices file is not an .xlsx workbook.'
      ],
      [
        409,
        { detail: 'Another import is still running. Wait for it to finish.' },
        'Another import is still running. Wait for it to finish.'
      ],
      [429, { detail: 'slow' }, 'Too many imports in a minute. Wait a moment, then try again.']
    ];
    for (const [status, body, error] of cases) {
      importApi((call) => (call.method === 'POST' ? refuse(status, body) : undefined));
      const refused = await importing.actions.start(
        upload({ source: 'mercadolibre', mode: 'preview' }, BOTH())
      );
      expect(refused).toMatchObject({ status, data: { error } });
    }
  });

  it("(+) a run's page shows its status and shaped report", async () => {
    importApi((call) => (call.path === '/catalog/import/runs/r1/' ? RUN : undefined));
    const data = await loaded(importRun.load(event({}, { id: 'r1' })));
    expect([data.run.status, data.run.dry_run, data.run.files_kept]).toEqual(['DONE', true, true]);
    expect(data.run.report.empty_categories).toEqual([
      'Sudaderas y hoodies: no products, all 15 of its rows were rejected.'
    ]);
  });

  it('(-) a run that is not there is a 404 page', async () => {
    importApi((call) => (call.path === '/catalog/import/runs/gone/' ? refuse(404, {}) : undefined));
    await expect(importRun.load(event({}, { id: 'gone' }))).rejects.toMatchObject({ status: 404 });
  });

  it('(+) Import these files imports the preview and opens the new run', async () => {
    importApi();
    const moved = await redirectOf(importRun.actions.import(upload({ notify: 'on' }, {})));
    expect(moved).toMatchObject({ status: 303, location: '/invoices/catalog/import/r2' });
    expect(writes()).toEqual([
      { path: '/catalog/import/runs/r1/import/', method: 'POST', body: { notify: true } }
    ]);
  });
});

// R8 The sales channel on an invoice --------------------------------------------------------

describe('R8 the sales channel on an invoice', () => {
  it('(+) a new invoice offers the channels in use, stores marked as such', async () => {
    catalogApi((call) =>
      call.path.startsWith('/catalog/channels/') ? undefined : { results: [], definitions: [] }
    );
    const data = await loaded(newInvoice.load(event()));
    expect(data.channels).toEqual([
      { id: 'ch1', name: 'Mercado Libre', kind: 'ONLINE' },
      { id: 'ch2', name: 'Tienda Centro', kind: 'STORE' }
    ]);
  });

  it('(+) without the channels the invoice form still loads, with none offered', async () => {
    catalogApi((call) =>
      call.path.startsWith('/catalog/channels/')
        ? refuse(500, {})
        : { results: [], definitions: [] }
    );
    const data = await loaded(newInvoice.load(event()));
    expect(data.channels).toEqual([]);
  });

  it('(+) an invoice shows the channel it was sold through', async () => {
    catalogApi(() => ({
      invoice: {
        id: 'i1',
        status: 'Draft',
        line_items: [],
        sales_channel: { id: 'ch2', name: 'Tienda Centro', kind: 'STORE' }
      },
      custom_field_definitions: []
    }));
    const shown = await getInvoice({ cookies: { get: () => undefined } as never }, 'i1');
    expect(shown.invoice.sales_channel).toEqual({
      id: 'ch2',
      name: 'Tienda Centro',
      kind: 'STORE'
    });
  });
});
