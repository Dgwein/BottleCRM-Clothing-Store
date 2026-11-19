/**
 * The catalog's web data layer: filters and paging, the validation every
 * form runs before it sends anything (W-cases in the catalog test plan), the
 * calls each write makes, and how a refusal becomes a sentence.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('#lib/api-helpers.js', () => ({ apiRequest: vi.fn() }));
vi.mock('./organization.js', () => ({ viewerIsAdmin: vi.fn(() => false) }));

const { apiRequest } = await import('#lib/api-helpers.js');
const catalog = await import('#lib/server/v2/catalog.js');

const cookies = {} as never;
const mocked = vi.mocked(apiRequest);

beforeEach(() => mocked.mockReset());

const formOf = (fields: Record<string, string | string[]>) => {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    for (const v of Array.isArray(value) ? value : [value]) form.append(key, v);
  }
  return form;
};

const apiError = (status: number, body: Record<string, unknown>) =>
  Object.assign(new Error(`HTTP ${status}`), { status, body });

const NO_FILTERS = { q: '', category: '', size: '', in_stock: '', offset: 0 } as const;

describe('W1 filters and paging', () => {
  it('(+) keeps a well-formed category, the stock filter and the offset', () => {
    const id = '3f2b8f9e-2c1d-4a5b-9c8d-1e2f3a4b5c6d';
    const f = catalog.readCatalogFilters(
      new URL(`https://x/c?category=${id}&in_stock=true&offset=50&size=2A`)
    );
    expect(f).toEqual({ q: '', category: id, size: '2A', in_stock: 'true', offset: 50 });
  });

  it('(-) drops a malformed category, an unknown stock value and a bad offset', () => {
    const f = catalog.readCatalogFilters(
      new URL('https://x/c?category=1%20OR%201&in_stock=maybe&offset=-5')
    );
    expect(f).toEqual({ ...NO_FILTERS });
    expect(catalog.readCatalogFilters(new URL('https://x/c?offset=abc')).offset).toBe(0);
  });

  it('(-) trims the search and caps its length', () => {
    expect(catalog.readCatalogFilters(new URL(`https://x/c?q=${'a'.repeat(500)}`)).q).toHaveLength(
      200
    );
    expect(catalog.readCatalogFilters(new URL('https://x/c?q=%20vestido%20')).q).toBe('vestido');
  });

  it('(+) sends size and stock as their own filters, a page of 50, and nothing empty', () => {
    const params = catalog.catalogQuery({ ...NO_FILTERS, size: '0-3 meses', in_stock: 'false' });
    expect(params.get('size')).toBe('0-3 meses');
    expect(params.get('in_stock')).toBe('false');
    expect(params.get('limit')).toBe('50');
    expect(params.has('search')).toBe(false);
    expect(params.has('attr')).toBe(false);
  });

  it('(+) builds the previous and next page links keeping the filters', () => {
    const filters = { ...NO_FILTERS, q: 'vestido rojo', size: '2A' };
    expect(catalog.pageQuery(filters, 50)).toBe('?q=vestido%20rojo&size=2A&offset=50');
    expect(catalog.pageQuery(filters, 0)).toBe('?q=vestido%20rojo&size=2A');
    expect(catalog.pageQuery({ ...NO_FILTERS }, 0)).toBe('');
  });
});

describe('W2 showing characteristics', () => {
  it('(+) joins lists, words yes and no, and keeps a unit', () => {
    expect(catalog.attributeText(['Fiesta', 'Diario'])).toBe('Fiesta, Diario');
    expect(catalog.attributeText(true)).toBe('Yes');
    expect(catalog.attributeText(false)).toBe('No');
    expect(catalog.attributeText(35, 'cm')).toBe('35 cm');
    expect(catalog.attributeText(null)).toBe('');
  });
});

describe('W3 product form', () => {
  const category = '3f2b8f9e-2c1d-4a5b-9c8d-1e2f3a4b5c6d';

  it('(+) a new product needs a name and a category', () => {
    const parsed = catalog.parseProductForm(formOf({ name: ' Vestido ', category }), {
      creating: true
    });
    expect(parsed).toEqual({ ok: true, payload: { name: 'Vestido', description: '', category } });
  });

  it('(-) refuses no name, a name over 255 and no category', () => {
    const bad = [
      formOf({ name: '', category }),
      formOf({ name: 'x'.repeat(256), category }),
      formOf({ name: 'Vestido', category: '' })
    ];
    for (const form of bad)
      expect(catalog.parseProductForm(form, { creating: true }).ok).toBe(false);
  });

  it('(+) an edit sends whether the product sells, never the category', () => {
    const parsed = catalog.parseProductForm(formOf({ name: 'Vestido', is_active: 'false' }), {
      creating: false
    });
    expect(parsed).toEqual({
      ok: true,
      payload: { name: 'Vestido', description: '', is_active: false }
    });
  });
});

describe('W4 variant forms', () => {
  const axes = [
    { code: 'color', name: 'Color', is_required: true, options: [{ value: 'Rojo' }] },
    { code: 'fabric_design', name: 'Design', is_required: false, options: [{ value: 'Lisa' }] }
  ];

  it('(+) a price above 0 with two decimals; stock a whole number', () => {
    for (const ok of ['499', '499.5', '0.01']) expect(catalog.priceOk(ok)).toBe(true);
    for (const ok of ['0', '48']) expect(catalog.stockOk(ok)).toBe(true);
  });

  it('(-) refuses a price of 0, negative, three decimals, words or blank', () => {
    for (const bad of ['0', '0.00', '-1', '4.999', 'abc', ''])
      expect(catalog.priceOk(bad)).toBe(false);
    for (const bad of ['-1', '1.5', 'abc', '']) expect(catalog.stockOk(bad)).toBe(false);
  });

  it('(+) a new variant: size free text, stock 0 when blank, the axes picked', () => {
    const parsed = catalog.parseVariantForm(
      formOf({ price: '599.00', size: '2 Años', stock: '', 'axis:color': 'Rojo' }),
      axes
    );
    expect(parsed).toEqual({
      ok: true,
      payload: {
        price: '599.00',
        stock: 0,
        size: '2 Años',
        sku: null,
        attributes: { color: 'Rojo' }
      }
    });
  });

  it('(-) refuses no price, a missing required axis and an option that does not exist', () => {
    const cases: [Record<string, string>, string][] = [
      [{ price: '', 'axis:color': 'Rojo' }, 'price above 0'],
      [{ price: '0', 'axis:color': 'Rojo' }, 'price above 0'],
      [{ price: '10', stock: '-2', 'axis:color': 'Rojo' }, 'whole number'],
      [{ price: '10' }, 'Pick a color'],
      [{ price: '10', 'axis:color': 'Verde' }, 'not a color option']
    ];
    for (const [fields, message] of cases) {
      const parsed = catalog.parseVariantForm(formOf(fields), axes);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.error).toContain(message);
    }
  });

  it('(+) a variant row sends price, stock, size and whether it sells', () => {
    const parsed = catalog.parseVariantRow(
      formOf({ price: '459.00', stock: '7', size: '3A', is_active: 'false' })
    );
    expect(parsed).toEqual({
      ok: true,
      payload: { price: '459.00', stock: 7, size: '3A', is_active: false }
    });
  });

  it('(-) a variant row without a price is refused', () => {
    expect(catalog.parseVariantRow(formOf({ price: '', stock: '1' })).ok).toBe(false);
  });
});

describe('W5 characteristics form', () => {
  type Definition = Parameters<typeof catalog.parseCharacteristics>[1][number];
  const attr = (
    code: string,
    data_type: Definition['data_type'],
    extra: Partial<Definition> = {}
  ): Definition => ({
    id: code,
    code,
    name: code,
    data_type,
    is_multivalued: false,
    unit: '',
    options: [] as { id: string; value: string; sort_order: number }[],
    ...extra
  });
  const attributes = [
    attr('material', 'OPTION', { options: [{ id: 'o1', value: 'Algodón', sort_order: 0 }] }),
    attr('occasions', 'OPTION', {
      is_multivalued: true,
      options: [
        { id: 'o2', value: 'Fiesta', sort_order: 0 },
        { id: 'o3', value: 'Diario', sort_order: 1 }
      ]
    }),
    attr('width', 'NUMBER', { unit: 'cm' }),
    attr('is_sportive', 'BOOLEAN'),
    attr('model', 'TEXT')
  ];

  it('(+) reads each type, and an empty field removes that characteristic', () => {
    const parsed = catalog.parseCharacteristics(
      formOf({
        'attr:material': 'Algodón',
        'attr:occasions': ['Fiesta', 'Diario'],
        'attr:width': '35',
        'unit:width': 'cm',
        'attr:is_sportive': 'false',
        'attr:model': ''
      }),
      attributes
    );
    expect(parsed).toEqual({
      ok: true,
      payload: {
        material: 'Algodón',
        occasions: ['Fiesta', 'Diario'],
        width: { value: '35', unit: 'cm' },
        is_sportive: false,
        model: null
      }
    });
  });

  it('(-) refuses an unknown option, a non-number and a non yes/no', () => {
    const cases: Record<string, string | string[]>[] = [
      { 'attr:material': 'Lino' },
      { 'attr:occasions': ['Boda'] },
      { 'attr:width': 'ancho' },
      { 'attr:is_sportive': 'maybe' },
      { 'attr:model': 'x'.repeat(501) }
    ];
    for (const fields of cases) {
      expect(catalog.parseCharacteristics(formOf(fields), attributes).ok).toBe(false);
    }
  });
});

describe('W6 category, attribute and option forms', () => {
  it('(+) accepts a good category, link, attribute and option', () => {
    expect(catalog.parseCategoryForm(formOf({ name: 'Pijamas' }))).toEqual({
      ok: true,
      payload: { name: 'Pijamas' }
    });
    const attribute = '3f2b8f9e-2c1d-4a5b-9c8d-1e2f3a4b5c6d';
    expect(
      catalog.parseLinkForm(formOf({ attribute, role: 'VARIANT_AXIS', is_required: 'true' }))
    ).toEqual({ ok: true, payload: { attribute, role: 'VARIANT_AXIS', is_required: true } });
    expect(
      catalog.parseAttributeForm(
        formOf({ code: 'season', name: 'Temporada', data_type: 'OPTION', is_multivalued: 'true' }),
        { creating: true }
      )
    ).toEqual({
      ok: true,
      payload: {
        code: 'season',
        name: 'Temporada',
        data_type: 'OPTION',
        is_multivalued: true,
        unit: ''
      }
    });
    expect(catalog.parseOptionForm(formOf({ value: 'Verano' }))).toEqual({
      ok: true,
      payload: { value: 'Verano' }
    });
  });

  it('(-) refuses blank names, a bad code, a multi-valued text and an unknown role', () => {
    const refused = [
      catalog.parseCategoryForm(formOf({ name: '' })),
      catalog.parseLinkForm(formOf({ attribute: 'nope', role: 'CHARACTERISTIC' })),
      catalog.parseLinkForm(
        formOf({ attribute: '3f2b8f9e-2c1d-4a5b-9c8d-1e2f3a4b5c6d', role: 'OWNER' })
      ),
      catalog.parseAttributeForm(formOf({ code: 'Bad Code', name: 'X', data_type: 'TEXT' }), {
        creating: true
      }),
      catalog.parseAttributeForm(
        formOf({ code: 'notes', name: 'Notas', data_type: 'TEXT', is_multivalued: 'true' }),
        { creating: true }
      ),
      catalog.parseAttributeForm(formOf({ name: '' }), { creating: false }),
      catalog.parseOptionForm(formOf({ value: ' ' }))
    ];
    for (const parsed of refused) expect(parsed.ok).toBe(false);
  });

  it('(+) an edit never sends an attribute code', () => {
    const attribute = catalog.parseAttributeForm(formOf({ code: 'x', name: 'Color' }), {
      creating: false
    });
    expect(attribute).toEqual({ ok: true, payload: { name: 'Color', unit: '' } });
  });
});

describe('W7 calls and refusals', () => {
  it('(+) each write calls its endpoint with its method', async () => {
    mocked.mockResolvedValue({});
    await catalog.updateVariant({ cookies }, 'v1', { price: '1.00' });
    await catalog.deleteProduct({ cookies }, 'p1');
    await catalog.linkAttribute({ cookies }, 'c1', { attribute: 'a1', role: 'CHARACTERISTIC' });
    await catalog.renameOption({ cookies }, 'o1', { value: 'Rojo' });
    await catalog.deleteChannel({ cookies }, 'ch1');
    expect(mocked.mock.calls.map(([path, options]) => [path, options?.method])).toEqual([
      ['/catalog/variants/v1/', 'PATCH'],
      ['/catalog/products/p1/', 'DELETE'],
      ['/catalog/categories/c1/attributes/', 'POST'],
      ['/catalog/attribute-options/o1/', 'PATCH'],
      ['/catalog/channels/ch1/', 'DELETE']
    ]);
  });

  it("(-) a refusal reads DRF's detail, then the per-field message", () => {
    expect(catalog.catalogError(apiError(409, { detail: 'Vestido is on 1 invoice.' }), 'x')).toBe(
      'Vestido is on 1 invoice.'
    );
    expect(catalog.catalogError(apiError(400, { message: 'Bad.' }), 'fallback')).toBe('Bad.');
    const moduleOff = {
      error: true,
      errors:
        'Tienda Centro has 1 invoice. Invoices is switched off, so switch it on to review it before deleting.',
      code: 'module_off'
    };
    expect(catalog.catalogError(apiError(409, moduleOff), 'x')).toBe(moduleOff.errors);
  });

  it('(-) attempt turns 403, 404, 409 and 400 into the matching failure', async () => {
    const cases: [number, Record<string, unknown>, string][] = [
      [403, { detail: 'no' }, 'Only an administrator can change the catalog.'],
      [404, {}, 'That is no longer in the catalog.'],
      [409, { detail: 'Products still use this attribute.' }, 'Products still use this attribute.'],
      [400, { message: 'This field may not be blank.' }, 'This field may not be blank.']
    ];
    for (const [status, body, message] of cases) {
      const refused = await catalog.attempt(
        () => Promise.reject(apiError(status, body)),
        'fallback',
        'create'
      );
      expect(refused).toMatchObject({ status, data: { form: 'create', error: message } });
    }
    expect(await catalog.attempt(() => Promise.resolve({}), 'x', 'create')).toBeNull();
  });
});

const NO_ADDRESS = {
  address_line: '',
  city: '',
  state: '',
  postcode: '',
  country: '',
  phone: ''
};

describe('W8 sales channel form', () => {
  it('(+) an online channel sends its market id and an empty address', () => {
    expect(
      catalog.parseChannelForm(
        formOf({ name: 'Amazon', code: 'amazon', kind: 'ONLINE', site: 'A1AM', city: 'Puebla' }),
        { creating: true }
      )
    ).toEqual({
      ok: true,
      payload: {
        name: 'Amazon',
        kind: 'ONLINE',
        site: 'A1AM',
        is_active: true,
        ...NO_ADDRESS,
        code: 'amazon'
      }
    });
  });

  it('(+) a physical store sends its address and phone and no market id', () => {
    const parsed = catalog.parseChannelForm(
      formOf({
        name: 'Tienda Centro',
        code: 'centro',
        kind: 'STORE',
        site: 'MLM',
        address_line: 'Av. Juárez 12',
        city: 'Puebla',
        state: 'Puebla',
        postcode: '72000',
        country: 'mx',
        phone: '+52 222 123 4567'
      }),
      { creating: true }
    );
    expect(parsed).toEqual({
      ok: true,
      payload: {
        name: 'Tienda Centro',
        kind: 'STORE',
        site: '',
        is_active: true,
        address_line: 'Av. Juárez 12',
        city: 'Puebla',
        state: 'Puebla',
        postcode: '72000',
        country: 'MX',
        phone: '+52 222 123 4567',
        code: 'centro'
      }
    });
  });

  it('(-) refuses no name, an unknown kind, a bad code, a bad country or phone', () => {
    const refused = [
      catalog.parseChannelForm(formOf({ name: '' }), { creating: false }),
      catalog.parseChannelForm(formOf({ name: 'X', kind: 'WAREHOUSE' }), { creating: false }),
      catalog.parseChannelForm(formOf({ name: 'Amazon', code: 'Amazon MX' }), { creating: true }),
      catalog.parseChannelForm(formOf({ name: 'T', kind: 'STORE', country: 'Mexico' }), {
        creating: false
      }),
      catalog.parseChannelForm(formOf({ name: 'T', kind: 'STORE', phone: 'call us' }), {
        creating: false
      })
    ];
    for (const parsed of refused) expect(parsed.ok).toBe(false);
  });

  it('(+) an edit never sends the code', () => {
    const parsed = catalog.parseChannelForm(
      formOf({ name: 'ML', code: 'x', kind: 'ONLINE', is_active: 'false' }),
      { creating: false }
    );
    expect(parsed).toEqual({
      ok: true,
      payload: { name: 'ML', kind: 'ONLINE', site: '', is_active: false, ...NO_ADDRESS }
    });
  });
});

const xlsx = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const MB = 1024 * 1024;

const MERCADO_LIBRE = {
  code: 'mercadolibre',
  name: 'Mercado Libre',
  available: true,
  reason: '',
  files: [
    { field: 'characteristics', label: 'Characteristics (one sheet per category)' },
    { field: 'prices', label: 'Prices and sale conditions (Publicaciones)' }
  ],
  kind: 'ONLINE' as const
};
const AMAZON = {
  code: 'amazon',
  name: 'Amazon',
  available: false,
  reason: 'Importing from Amazon is not available yet: its report has to be analyzed first.',
  files: [],
  kind: 'ONLINE' as const
};

const reportForm = (files: Record<string, File>, extra: Record<string, string> = {}) => {
  const form = formOf(extra);
  for (const [key, file] of Object.entries(files)) form.set(key, file);
  return form;
};

describe("W9 importing a channel's report", () => {
  it('(+) a preview sends the source, both files, dry_run and the email choice', () => {
    const parsed = catalog.parseImportForm(
      reportForm(
        { characteristics: xlsx('characteristics.xlsx'), prices: xlsx('prices.xlsx') },
        { mode: 'preview', notify: 'on' }
      ),
      MERCADO_LIBRE
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const body = parsed.payload;
    expect([body.get('source'), body.get('dry_run'), body.get('notify')]).toEqual([
      'mercadolibre',
      'true',
      'true'
    ]);
    expect((body.get('characteristics') as File).name).toBe('characteristics.xlsx');
    expect((body.get('prices') as File).name).toBe('prices.xlsx');
  });

  it('(+) Import is not a dry run, and no tick sends no email request', () => {
    const parsed = catalog.parseImportForm(
      reportForm(
        { characteristics: xlsx('c.xlsx'), prices: xlsx('p.xlsx', 20 * MB) },
        { mode: 'import' }
      ),
      MERCADO_LIBRE
    );
    expect(parsed.ok && [parsed.payload.get('dry_run'), parsed.payload.get('notify')]).toEqual([
      'false',
      'false'
    ]);
  });

  it('(-) refuses a missing file, one that is not .xlsx, and one over 20 MB', () => {
    const cases: [Record<string, File>, string][] = [
      [{ prices: xlsx('p.xlsx') }, 'Choose the characteristics file.'],
      [{ characteristics: xlsx('c.xlsx') }, 'Choose the prices file.'],
      [
        { characteristics: xlsx('c.csv'), prices: xlsx('p.xlsx') },
        'The characteristics file must be an .xlsx workbook.'
      ],
      [
        { characteristics: xlsx('c.xlsx'), prices: xlsx('p.xlsx', 20 * MB + 1) },
        'The prices file exceeds the 20 MB limit.'
      ],
      [
        { characteristics: xlsx('c.xlsx', 0), prices: xlsx('p.xlsx') },
        'Choose the characteristics file.'
      ]
    ];
    for (const [files, error] of cases) {
      expect(catalog.parseImportForm(reportForm(files), MERCADO_LIBRE)).toEqual({
        ok: false,
        error
      });
    }
  });

  it('(-) a source with no importer takes nothing and says why', () => {
    expect(catalog.parseImportForm(reportForm({ report: xlsx('amazon.xlsx') }), AMAZON)).toEqual({
      ok: false,
      error: AMAZON.reason
    });
  });

  it('(+) each call goes to its endpoint: sources, runs, a run, start, import a preview', async () => {
    mocked.mockImplementation(async (path?: string) =>
      String(path).includes('/sources/')
        ? { results: [{ ...MERCADO_LIBRE, channel: null }] }
        : String(path).includes('?limit')
          ? { count: 0, next: null, previous: null, results: [] }
          : { id: 'r1', file_names: {}, report: null }
    );
    await catalog.listImportSources({ cookies });
    await catalog.listImportRuns({ cookies });
    await catalog.getImportRun({ cookies }, 'r1');
    await catalog.startImport({ cookies }, new FormData());
    await catalog.importPreviewed({ cookies }, 'r1', true);
    expect(mocked.mock.calls.map(([path, options]) => [path, options?.method ?? 'GET'])).toEqual([
      ['/catalog/import/sources/', 'GET'],
      ['/catalog/import/runs/?limit=10', 'GET'],
      ['/catalog/import/runs/r1/', 'GET'],
      ['/catalog/import/runs/', 'POST'],
      ['/catalog/import/runs/r1/import/', 'POST']
    ]);
    expect(mocked.mock.calls[4][1]?.body).toEqual({ notify: true });
  });

  it('(+) the report reads as counts, notes and sentences; a category with no products is named', () => {
    const shaped = catalog.shapeReport({
      created: { variants: 8, products: 4, mystery_rows: 1 },
      updated: { channels: 1 },
      notes: { duplicate_publications_ignored: 3 },
      empty_categories: [
        { category: 'Sudaderas y hoodies', sheet: 'Sudaderas y hoodies', rows: 15, rejected: 15 },
        { category: 'Pijamas', sheet: 'Pijamas', rows: 0, rejected: 0 }
      ],
      warnings: ['w'],
      rejected: ['r1', 'r2']
    });
    expect(shaped.counts).toEqual([
      { label: 'Sales channels', created: 0, updated: 1 },
      { label: 'Products', created: 4, updated: 0 },
      { label: 'Variants', created: 8, updated: 0 },
      { label: 'mystery rows', created: 1, updated: 0 }
    ]);
    expect(shaped.notes).toEqual([{ label: 'Duplicate publications ignored', count: 3 }]);
    expect(shaped.empty_categories).toEqual([
      'Sudaderas y hoodies: no products, all 15 of its rows were rejected.',
      'Pijamas: no products, its sheet has no rows.'
    ]);
    expect([shaped.rejected, shaped.warnings]).toEqual([['r1', 'r2'], ['w']]);
  });
});
