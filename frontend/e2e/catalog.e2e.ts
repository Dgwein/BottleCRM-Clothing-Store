/**
 * The catalog in a browser: the V-cases of the catalog test plan. A real
 * Chromium drives the running app (backend and frontend) the way a person
 * does, checks what each page shows, and checks the result against the API.
 * See e2e/README.md for how to run it.
 *
 *   node e2e/catalog.e2e.ts <seed.json> [--base URL] [--api URL] [--shots DIR]
 */
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { resolve as resolvePath } from 'node:path';

type Tokens = { access: string; refresh: string };
type Seed = {
  org: string;
  admin: Tokens;
  member: Tokens;
  sold_product: string;
  sold_product_name: string;
  products: number;
  rejected: number;
};
type Kind = '+' | '-';

// The part of puppeteer's API these tests use; puppeteer is not an app
// dependency, so its own types are not available here.
type Handle = {
  click(): Promise<void>;
  uploadFile(...paths: string[]): Promise<void>;
  evaluate<R>(fn: (element: HTMLInputElement) => R): Promise<R>;
};
type Page = {
  goto(url: string, options: { waitUntil: 'networkidle2' }): Promise<unknown>;
  url(): string;
  evaluate<R, A extends unknown[] = []>(fn: (...args: A) => R, ...args: A): Promise<R>;
  waitForFunction<A>(
    fn: (arg: A) => unknown,
    options?: { timeout?: number },
    arg?: A
  ): Promise<unknown>;
  waitForSelector(selector: string, options?: { timeout?: number }): Promise<Handle>;
  $(selector: string): Promise<Handle>;
  $eval<R, E extends Element = HTMLInputElement>(
    selector: string,
    fn: (element: E) => R
  ): Promise<R>;
  $$eval<R, E extends Element = Element>(selector: string, fn: (elements: E[]) => R): Promise<R>;
  select(selector: string, ...values: string[]): Promise<string[]>;
  type(selector: string, text: string): Promise<void>;
  screenshot(options: { path: string; fullPage: boolean }): Promise<unknown>;
  setViewport(viewport: { width: number; height: number }): Promise<void>;
  setExtraHTTPHeaders(headers: Record<string, string>): Promise<void>;
  setCookie(
    ...cookies: { name: string; value: string; domain: string; path: string }[]
  ): Promise<void>;
};
type Browser = { newPage(): Promise<Page>; close(): Promise<void> };

const argv = process.argv.slice(2);
const option = (name: string, fallback: string) => {
  const at = argv.indexOf(name);
  return at >= 0 ? argv[at + 1] : fallback;
};
const seed: Seed = JSON.parse(readFileSync(argv[0], 'utf8'));
const BASE = option('--base', 'http://localhost:5181');
const API = option('--api', 'http://localhost:8000');
const SHOTS = option('--shots', 'e2e-screenshots');
const MAIL = option('--mail-dir', '/tmp/e2e-mail');
const REPORT = option(
  '--report',
  fileURLToPath(new URL('../../backend/catalog/tests/mlInfo/', import.meta.url))
);
mkdirSync(SHOTS, { recursive: true });

// puppeteer-core (and @sparticuz/chromium when CHROME_PATH is not set) are
// not app dependencies: they are resolved from E2E_DEPS, see the README.
const deps = createRequire(pathToFileURL(`${process.env.E2E_DEPS ?? process.cwd()}/`).href);
const load = async (name: string) => (await import(pathToFileURL(deps.resolve(name)).href)).default;
const puppeteer = await load('puppeteer-core');
let executablePath = process.env.CHROME_PATH;
let args: string[] = [];
if (!executablePath) {
  const chromium = await load('@sparticuz/chromium');
  executablePath = await chromium.executablePath();
  // Its Lambda defaults turn web security off, and with it the Origin header
  // every form POST needs; a normal browser sends it.
  args = chromium.args.filter((flag: string) => flag !== '--disable-web-security');
}
const browser: Browser = await puppeteer.launch({ executablePath, args, headless: true });

/** A browser tab signed in as `who`, at a desktop or phone width. */
async function signedIn(who: Tokens, width = 1366) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 900 });
  // Stands in for the proxy over plain http: the server runs with
  // PROTOCOL_HEADER=x-forwarded-proto (see the Dockerfile and the README).
  await page.setExtraHTTPHeaders({ 'x-forwarded-proto': new URL(BASE).protocol.replace(':', '') });
  const host = new URL(BASE).hostname;
  await page.setCookie(
    { name: 'jwt_access', value: who.access, domain: host, path: '/' },
    { name: 'jwt_refresh', value: who.refresh, domain: host, path: '/' },
    { name: 'org', value: seed.org, domain: host, path: '/' }
  );
  return page;
}

async function api(path: string) {
  const response = await fetch(`${API}/api${path}`, {
    headers: { Authorization: `Bearer ${seed.admin.access}` }
  });
  return { status: response.status, body: response.status === 204 ? null : await response.json() };
}

const bodyText = (page: Page): Promise<string> => page.evaluate(() => document.body.innerText);

/** Wait until the page says `text` (a form result, a new row). */
async function waitForText(page: Page, text: string, timeout = 15000) {
  await page.waitForFunction((t: string) => document.body.innerText.includes(t), { timeout }, text);
}

/** Wait until `test(arg)` holds in the page (an address, a text, a row). */
async function until(page: Page, test: (arg: string) => boolean, arg = '', timeout = 15000) {
  await page.waitForFunction(test, { timeout }, arg);
}

// networkidle2, not 0: the app keeps a background connection open.
async function open(page: Page, path: string) {
  await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle2' });
}

/** Click the button or link whose text is exactly `text`, inside `scope`. */
async function click(page: Page, text: string, scope = 'body') {
  const handle = await page.waitForSelector(`${scope} ::-p-text(${text})`, { timeout: 10000 });
  await handle.click();
}

/** Click the element `selector` names. */
async function press(page: Page, selector: string) {
  const handle = await page.waitForSelector(selector, { timeout: 10000 });
  await handle.click();
}

/** Submit or follow, then wait for what the page should say next. */
async function submitted(
  page: Page,
  action: () => Promise<void>,
  expect?: string,
  timeout = 15000
) {
  await action();
  if (expect) await waitForText(page, expect, timeout);
}

function check(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

const results: { id: string; kind: Kind; title: string; ok: boolean; detail: string }[] = [];

async function step(id: string, kind: Kind, title: string, run: () => Promise<Page>) {
  // A failed step is pictured on the admin tab, where most steps run.
  let page: Page = admin;
  try {
    page = await run();
    results.push({ id, kind, title, ok: true, detail: '' });
  } catch (err) {
    results.push({ id, kind, title, ok: false, detail: String((err as Error).message ?? err) });
  }
  await page.screenshot({ path: `${SHOTS}/${id}.png`, fullPage: true });
  const last = results[results.length - 1];
  console.log(
    `${last.ok ? 'PASS' : 'FAIL'}  ${id} (${kind}) ${title}${last.ok ? '' : `\n      ${last.detail}`}`
  );
}

const admin = await signedIn(seed.admin);
const rows = (page: Page): Promise<number> =>
  page.evaluate(() => document.querySelectorAll('table.v2-table tbody tr').length);

// The product list: query, paging, filters ---------------------------------

await step(
  'V1',
  '+',
  'the list shows every product, 50 to a page, with the catalog tabs',
  async () => {
    await open(admin, '/invoices/catalog');
    const text = await bodyText(admin);
    check(text.includes(`${seed.products} products`), `expected "${seed.products} products"`);
    check(text.includes(`1 to 50 of ${seed.products}`), 'expected the range "1 to 50 of N"');
    check((await rows(admin)) === 50, 'expected 50 rows');
    for (const tab of ['Products', 'Categories', 'Attributes', 'Sales channels', 'Import']) {
      check(text.includes(tab), `expected the ${tab} tab`);
    }
    return admin;
  }
);

await step('V2', '+', 'Next and Previous page through the list', async () => {
  await submitted(admin, () => click(admin, 'Next'), `51 to ${seed.products} of`);
  check(admin.url().includes('offset=50'), 'expected offset=50 in the address');
  const text = await bodyText(admin);
  check(text.includes(`51 to ${seed.products} of ${seed.products}`), 'expected the second range');
  check(!text.includes('Next'), 'the last page has no Next');
  check((await rows(admin)) === seed.products - 50, 'expected the remaining rows');
  await submitted(admin, () => click(admin, 'Previous'), `1 to 50 of ${seed.products}`);
  check(
    (await bodyText(admin)).includes(`1 to 50 of ${seed.products}`),
    'expected the first page again'
  );
  return admin;
});

await step('V3', '+', 'filters by size, category, stock and search match the API', async () => {
  const cases: [string, string][] = [
    ['size=2A', 'size=2A'],
    ['size=2%20A%C3%B1os', 'size=2%20A%C3%B1os'],
    ['in_stock=false', 'in_stock=false'],
    ['q=Plim', 'search=Plim']
  ];
  for (const [page, query] of cases) {
    const expected = (await api(`/catalog/products/?limit=1&${query}`)).body.count;
    await open(admin, `/invoices/catalog?${page}`);
    const text = await bodyText(admin);
    check(text.includes(`${expected} product`), `${page}: expected ${expected} products`);
  }
  await open(admin, '/invoices/catalog');
  await admin.select('select[name="size"]', '2A');
  await submitted(admin, () => click(admin, 'Filter'), 'products match');
  await until(admin, () => location.search.includes('size=2A'));
  const sizes: string[] = await admin.$$eval(
    'table.v2-table tbody tr td.sizes',
    (cells: Element[]) => cells.map((c) => (c as HTMLElement).innerText)
  );
  check(
    sizes.length > 0 && sizes.every((s) => s.split(', ').includes('2A')),
    'every row comes in 2A'
  );
  return admin;
});

await step(
  'V4',
  '-',
  'a filter nothing matches shows "Nothing matches", not an error',
  async () => {
    await open(admin, '/invoices/catalog?size=Nada');
    check((await bodyText(admin)).includes('Nothing matches'), 'expected the empty state');
    return admin;
  }
);

// One product: visualization and manipulation ------------------------------

const classic = seed.sold_product;
const variantLabel = (size: string) => `${seed.sold_product_name} (${size} / Rojo / Lisa)`;

await step(
  'V5',
  '+',
  'a product page shows its sizes, stock, characteristics and listing',
  async () => {
    await open(admin, '/invoices/catalog?q=MLM4549556180');
    check((await rows(admin)) === 1, 'the listing id finds one product');
    await submitted(admin, () => click(admin, seed.sold_product_name), 'MLM4549556180');
    check(admin.url().endsWith(`/invoices/catalog/${classic}`), 'opened the product page');
    const text = await bodyText(admin);
    for (const size of ['12 meses', '18 meses', '2A', '3A'])
      check(text.includes(size), `size ${size}`);
    check(text.includes('MLM4549556180'), 'the listing id');
    check(text.includes('Material') && text.includes('Tela'), 'the material characteristic');
    const stock = await admin.$eval(
      `input[aria-label="Stock of ${variantLabel('18 meses')}"]`,
      (i: HTMLInputElement) => i.value
    );
    check(stock === '18', `expected stock 18, saw ${stock}`);
    return admin;
  }
);

await step('V6', '-', 'a price of 0 is refused on the page and nothing changes', async () => {
  const price = `input[aria-label="Price of ${variantLabel('3A')}"]`;
  await admin.$eval(price, (i: HTMLInputElement) => (i.value = ''));
  await admin.type(price, '0');
  const row = await admin.$(`${price}`);
  await submitted(admin, () => row.evaluate((i: HTMLInputElement) => i.form?.requestSubmit()));
  await waitForText(admin, 'Enter a price above 0');
  const stored = (await api(`/catalog/products/${classic}/`)).body.variants.find(
    (v: { size: string }) => v.size === '3A'
  );
  check(stored.price === '619.00', `the price stayed 619.00, saw ${stored.price}`);
  return admin;
});

await step('V7', '+', 'a variant row saves a new price and stock', async () => {
  await open(admin, `/invoices/catalog/${classic}`);
  const price = `input[aria-label="Price of ${variantLabel('3A')}"]`;
  const stock = `input[aria-label="Stock of ${variantLabel('3A')}"]`;
  await admin.$eval(price, (i: HTMLInputElement) => (i.value = '649.00'));
  await admin.$eval(stock, (i: HTMLInputElement) => (i.value = '7'));
  const input = await admin.$(price);
  await submitted(admin, () => input.evaluate((i: HTMLInputElement) => i.form?.requestSubmit()));
  await waitForText(admin, 'Saved');
  const stored = (await api(`/catalog/products/${classic}/`)).body.variants.find(
    (v: { size: string }) => v.size === '3A'
  );
  check(
    stored.price === '649.00' && stored.stock === 7,
    `stored ${stored.price} / ${stored.stock}`
  );
  return admin;
});

await step('V8', '+', 'a new variant is added; the same combination again is refused', async () => {
  await open(admin, `/invoices/catalog/${classic}`);
  await click(admin, 'Add a variant');
  const form = 'form[action="?/addVariant"]';
  await admin.type(`${form} input[name="size"]`, '4A');
  await admin.type(`${form} input[name="price"]`, '659.00');
  await admin.$eval(`${form} input[name="stock"]`, (i: HTMLInputElement) => (i.value = '5'));
  await admin.select(`${form} select[name="axis:color"]`, 'Rojo');
  await admin.select(`${form} select[name="axis:fabric_design"]`, 'Lisa');
  await submitted(admin, () => click(admin, 'Add variant', form));
  await waitForText(admin, 'Variant added');
  check((await bodyText(admin)).includes('4A'), 'the 4A row is shown');
  // The section stays open after a save, with the form emptied for the next.
  await admin.type(`${form} input[name="size"]`, '4A');
  await admin.type(`${form} input[name="price"]`, '659.00');
  await admin.select(`${form} select[name="axis:color"]`, 'Rojo');
  await admin.select(`${form} select[name="axis:fabric_design"]`, 'Lisa');
  await submitted(admin, () => click(admin, 'Add variant', form));
  await waitForText(admin, 'Another variant of this product has this size and these values.');
  return admin;
});

await step('V9', '+', 'characteristics are edited on the page', async () => {
  await open(admin, `/invoices/catalog/${classic}`);
  await click(admin, 'Edit characteristics');
  const model = 'form[action="?/characteristics"] input[name="attr:model"]';
  await admin.$eval(model, (i: HTMLInputElement) => (i.value = 'Plim Plim Fiesta'));
  await submitted(admin, () => click(admin, 'Save characteristics'), 'Saved');
  await open(admin, `/invoices/catalog/${classic}`);
  check((await bodyText(admin)).includes('Plim Plim Fiesta'), 'the new model is shown');
  const stored = (await api(`/catalog/products/${classic}/`)).body.attributes.find(
    (a: { code: string }) => a.code === 'model'
  );
  check(stored.value === 'Plim Plim Fiesta', 'and stored once');
  return admin;
});

await step(
  'V10',
  '-',
  'a product sold on an invoice is not deleted, and the page says why',
  async () => {
    await open(admin, `/invoices/catalog/${classic}`);
    await click(admin, 'Delete product');
    await submitted(admin, () => click(admin, 'Delete', 'form.v2-confirm'));
    await waitForText(admin, 'is on 1 invoice, so it cannot be deleted');
    check(
      (await bodyText(admin)).includes('Variants with sales: 12 meses'),
      'the refusal names the variant with sales'
    );
    check(
      (await api(`/catalog/products/${classic}/`)).status === 200,
      'the product is still there'
    );
    return admin;
  }
);

await step(
  'V11',
  '+',
  'New product creates a product and opens it; a blank name is refused',
  async () => {
    await open(admin, '/invoices/catalog');
    await submitted(admin, () => click(admin, 'New product'));
    await until(admin, () => location.pathname.endsWith('/new'));
    await admin.type('input[name="name"]', '   ');
    const vestidos = await admin.$$eval(
      'select[name="category"] option',
      (options: HTMLOptionElement[]) =>
        options.find((o) => o.textContent === 'Vestidos')?.value ?? ''
    );
    await admin.select('select[name="category"]', vestidos);
    await submitted(admin, () => click(admin, 'Add product'));
    await waitForText(admin, 'Give the product a name.');
    await admin.$eval('input[name="name"]', (i: HTMLInputElement) => (i.value = 'Vestido E2E'));
    await submitted(admin, () => click(admin, 'Add product'));
    await until(admin, () => !location.pathname.endsWith('/new'));
    await waitForText(admin, 'No variants yet');
    const text = await bodyText(admin);
    check(text.includes('Vestido E2E') && text.includes('No variants yet'), 'the new product page');
    return admin;
  }
);

await step('V12', '+', 'an unsold product is deleted and the list no longer has it', async () => {
  const id = admin.url().split('/').pop();
  await click(admin, 'Delete product');
  await submitted(admin, () => click(admin, 'Delete', 'form.v2-confirm'));
  await admin.waitForFunction(() => location.pathname === '/invoices/catalog');
  check((await api(`/catalog/products/${id}/`)).status === 404, 'the product is gone');
  return admin;
});

// Categories, attributes and sales channels ---------------------------------

await step(
  'V13',
  '+',
  'categories: add, link an attribute as an axis, delete; refusals shown',
  async () => {
    await open(admin, '/invoices/catalog/categories');
    const categories = (await api('/catalog/categories/')).body.results;
    check((await bodyText(admin)).includes('8 categories'), 'the report has 8 categories');
    check(categories.length === 8, 'and so does the API');
    const create = 'form[action="?/create"]';
    await admin.type(`${create} input[name="name"]`, 'Pijamas');
    await submitted(admin, () => click(admin, 'Add category'));
    await waitForText(admin, 'Pijamas');
    await admin.type(`${create} input[name="name"]`, 'Pijamas');
    await submitted(admin, () => click(admin, 'Add category'));
    await waitForText(admin, 'Another category has this name.');
    const section = 'section[aria-label="Pijamas"]';
    const color = await admin.$$eval(
      `${section} select[name="attribute"] option`,
      (options: HTMLOptionElement[]) => options.find((o) => o.textContent === 'Color')?.value ?? ''
    );
    await admin.select(`${section} select[name="attribute"]`, color);
    await admin.select(`${section} select[name="role"]`, 'VARIANT_AXIS');
    await submitted(admin, () =>
      press(admin, `${section} form[action="?/link"] button[type="submit"]`)
    );
    await admin.waitForFunction(
      (s: string) => document.querySelector(s)?.textContent?.includes('Variant axis'),
      {},
      section
    );
    await click(admin, 'Delete', 'section[aria-label="Vestidos"] header');
    await submitted(admin, () => click(admin, 'Delete category', 'section[aria-label="Vestidos"]'));
    await waitForText(admin, 'Products or other categories still use this category.');
    await click(admin, 'Delete', `${section} header`);
    await submitted(admin, () => click(admin, 'Delete category', section));
    await admin.waitForFunction((s: string) => !document.querySelector(s), {}, section);
    return admin;
  }
);

await step(
  'V14',
  '+',
  'attributes: add one with an option, delete it; refusals shown',
  async () => {
    await open(admin, '/invoices/catalog/attributes');
    const create = 'form[action="?/create"]';
    await admin.type(`${create} input[name="code"]`, 'color');
    await admin.type(`${create} input[name="name"]`, 'Otro color');
    await submitted(admin, () => click(admin, 'Add attribute'));
    await waitForText(admin, 'Another attribute has this code.');
    await admin.$eval(
      `${create} input[name="code"]`,
      (i: HTMLInputElement) => (i.value = 'temporada')
    );
    await admin.$eval(
      `${create} input[name="name"]`,
      (i: HTMLInputElement) => (i.value = 'Temporada')
    );
    await admin.select(`${create} select[name="is_multivalued"]`, 'true');
    await submitted(admin, () => click(admin, 'Add attribute'));
    const section = 'section[aria-label="Temporada"]';
    await admin.waitForSelector(section);
    await admin.type(`${section} form[action="?/addOption"] input[name="value"]`, 'Verano');
    await submitted(admin, () => click(admin, 'Add option', section));
    await admin.waitForSelector(`${section} input[aria-label="Option Verano"]`);
    await submitted(admin, () =>
      press(
        admin,
        'section[aria-label="Color"] li:has(input[aria-label="Option Rojo"]) form[action="?/deleteOption"] button'
      )
    );
    await waitForText(admin, 'Products still use this option.');
    await click(admin, 'Delete', `${section} header`);
    await submitted(admin, () => click(admin, 'Delete attribute', section));
    await admin.waitForFunction((s: string) => !document.querySelector(s), {}, section);
    return admin;
  }
);

await step(
  'V15',
  '+',
  'sales channels: add Amazon, switch it off, delete it; refusals shown',
  async () => {
    await open(admin, '/invoices/catalog/channels');
    check((await bodyText(admin)).includes('Mercado Libre'), 'the imported channel is listed');
    const create = 'form[action="?/create"]';
    await admin.type(`${create} input[name="name"]`, 'Amazon');
    await admin.type(`${create} input[name="code"]`, 'amazon');
    await submitted(admin, () => click(admin, 'Add channel'));
    const section = 'section[aria-label="Amazon"]';
    await admin.waitForSelector(section);
    await admin.type(`${create} input[name="name"]`, 'Amazon MX');
    await admin.type(`${create} input[name="code"]`, 'amazon');
    await submitted(admin, () => click(admin, 'Add channel'));
    await waitForText(admin, 'Another sales channel has this code.');
    await admin.select(`${section} select[name="is_active"]`, 'false');
    await submitted(admin, () =>
      press(admin, `${section} form[action="?/update"] button[type="submit"]`)
    );
    await admin.waitForFunction(
      (s: string) => document.querySelector(s)?.textContent?.includes('Switched off'),
      {},
      section
    );
    await click(admin, 'Delete', 'section[aria-label="Mercado Libre"] header');
    await submitted(admin, () =>
      click(admin, 'Delete channel', 'section[aria-label="Mercado Libre"]')
    );
    await waitForText(admin, 'listings, so it cannot be deleted. Switch it off instead.');
    await click(admin, 'Delete', `${section} header`);
    await submitted(admin, () => click(admin, 'Delete channel', section));
    await admin.waitForFunction((s: string) => !document.querySelector(s), {}, section);
    return admin;
  }
);

await step(
  'V18',
  '+',
  'a physical store is added with its address, and listed as a store',
  async () => {
    await open(admin, '/invoices/catalog/channels');
    const create = 'form[action="?/create"]';
    await admin.select(`${create} select[name="kind"]`, 'STORE');
    await admin.waitForSelector(`${create} input[name="city"]`);
    check(!(await admin.$(`${create} input[name="site"]`)), 'a store is offered no market id');
    await admin.type(`${create} input[name="name"]`, 'Tienda Centro');
    await admin.type(`${create} input[name="code"]`, 'centro');
    await admin.type(`${create} input[name="address_line"]`, 'Av. Juárez 12');
    await admin.type(`${create} input[name="city"]`, 'Puebla');
    await admin.select(`${create} select[name="country"]`, 'MX');
    await admin.type(`${create} input[name="phone"]`, '+52 222 123 4567');
    await submitted(admin, () => click(admin, 'Add channel'));
    const section = 'section[aria-label="Tienda Centro"]';
    await admin.waitForSelector(section);
    const card = await admin.$eval(section, (s: HTMLElement) => s.innerText);
    check(card.includes('Physical store'), 'marked as a physical store');
    check(card.includes('Av. Juárez 12, Puebla, Mexico'), `the address is shown: ${card}`);
    const stored = (await api('/catalog/channels/')).body.results.find(
      (c: { code: string }) => c.code === 'centro'
    );
    check(
      stored.kind === 'STORE' && stored.city === 'Puebla' && stored.site === '',
      'stored as a store'
    );
    return admin;
  }
);

/** Choose, in the select inside the label reading `label`, the first option whose text has `text`. */
async function chooseIn(page: Page, label: string, text: string) {
  const chosen = await page.evaluate(
    (want: string, has: string) => {
      const field = [...document.querySelectorAll('label')].find(
        (l) => l.querySelector('span')?.textContent?.trim() === want
      );
      const select = field?.querySelector('select');
      const option = [...(select?.options ?? [])].find((o) => (o.textContent ?? '').includes(has));
      if (!select || !option) return '';
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return option.value;
    },
    label,
    text
  );
  check(chosen, `no "${text}" to choose under ${label}`);
}

await step('V19', '+', 'a new invoice records the store it was sold in, and shows it', async () => {
  await open(admin, '/invoices/new');
  await chooseIn(admin, 'Account', 'Boutique Luna');
  await chooseIn(admin, 'Contact', 'Ana Luna');
  await chooseIn(admin, 'Sales channel', 'Tienda Centro (physical store)');
  await admin.type('input[placeholder="What this invoice covers"]', 'Venta en tienda');
  const added = await admin.evaluate(() => {
    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="Add a line from the catalogue"]'
    );
    const option = [...(select?.options ?? [])].find((o) =>
      (o.textContent ?? '').includes('Para Fiesta (18 meses')
    );
    if (!select || !option) return false;
    select.value = option.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  });
  check(added, 'the 18 meses dress is offered as a line');
  await submitted(admin, () => click(admin, 'Save as draft'));
  await until(admin, () => /^\/invoices\/[0-9a-f-]{36}$/.test(location.pathname));
  await waitForText(admin, 'sold through Tienda Centro');
  const id = admin.url().split('/').pop();
  const stored = (await api(`/invoices/${id}/`)).body.invoice;
  check(
    stored.sales_channel?.name === 'Tienda Centro' && stored.account?.name === 'Boutique Luna',
    'the store is the channel and Boutique Luna is still the customer'
  );
  check(
    stored.line_items[0]?.unit_price === '619.00',
    `sold at the variant's one price, saw ${stored.line_items[0]?.unit_price}`
  );
  return admin;
});

await step(
  'V20',
  '+',
  'an unsold variant of a product with sales is deleted; the product stays',
  async () => {
    await open(admin, `/invoices/catalog/${classic}`);
    const before = (await api(`/catalog/products/${classic}/`)).body.variants.length;
    const row = `tr:has(input[aria-label="Price of ${variantLabel('2A')}"])`;
    await click(admin, 'Delete', row);
    await submitted(admin, () => click(admin, 'Delete variant', row));
    await admin.waitForFunction(
      (s: string) => !document.querySelector(s),
      {},
      `input[aria-label="Price of ${variantLabel('2A')}"]`
    );
    const after = (await api(`/catalog/products/${classic}/`)).body;
    check(after.variants.length === before - 1, 'one variant fewer');
    check(!after.variants.some((v: { size: string }) => v.size === '2A'), 'the 2A dress is gone');
    return admin;
  }
);

// Importing a report from the page ---------------------------------------------

const ML_CARD = 'section[aria-label="Import from Mercado Libre"]';

async function chooseReport(page: Page, which: { characteristics?: string; prices?: string }) {
  for (const [name, file] of Object.entries(which)) {
    const input = await page.waitForSelector(`${ML_CARD} input[name="${name}"]`);
    await input.uploadFile(file);
  }
}

/** Start a run from the Mercado Libre card and wait for its page to say `done`. */
async function runFromCard(page: Page, button: 'Preview' | 'Import', done: string) {
  await press(page, `${ML_CARD} button[value="${button.toLowerCase()}"]`);
  await until(page, () => /^\/invoices\/catalog\/import\/[0-9a-f-]{36}$/.test(location.pathname));
  // The run page asks again every few seconds while the worker reads the report.
  await waitForText(page, done, 240000);
}

await step(
  'V21',
  '+',
  'a preview runs in the background, then shows what an import would do; nothing saved',
  async () => {
    const before = (await api('/catalog/products/?limit=1')).body.count;
    await open(admin, '/invoices/catalog/import');
    await chooseReport(admin, {
      characteristics: `${REPORT}characteristics.xlsx`,
      prices: `${REPORT}prices.xlsx`
    });
    await runFromCard(admin, 'Preview', 'Nothing was saved');
    const text = await bodyText(admin);
    check(
      text.includes('Sudaderas y hoodies: no products, all 15 of its rows were rejected.'),
      'the category with no products is named'
    );
    check(text.includes(`Rejected rows: ${seed.rejected}`), `${seed.rejected} rows rejected`);
    check(text.includes('Import these files'), 'the preview offers to import its files');
    check((await api('/catalog/products/?limit=1')).body.count === before, 'nothing was saved');
    return admin;
  }
);

await step(
  'V22',
  '+',
  '"Import these files" imports the preview; the variant deleted in V20 is back',
  async () => {
    await click(admin, 'Import these files');
    await until(admin, () => !document.body.innerText.includes('Import these files'));
    await waitForText(admin, 'Saved', 240000);
    const sizes = (await api(`/catalog/products/${classic}/`)).body.variants.map(
      (v: { size: string }) => v.size
    );
    check(sizes.includes('2A'), `the 2A dress is back: ${sizes.join(', ')}`);
    return admin;
  }
);

await step(
  'V23',
  '-',
  'a missing file, or one that is not a workbook, is refused at once',
  async () => {
    await open(admin, '/invoices/catalog/import');
    await admin.evaluate(() =>
      document.querySelectorAll('input[type="file"]').forEach((i) => i.removeAttribute('required'))
    );
    await chooseReport(admin, { prices: `${REPORT}prices.xlsx` });
    await submitted(
      admin,
      () => press(admin, `${ML_CARD} button[value="import"]`),
      'Choose the characteristics file.'
    );
    // Normalized: the browser reads no file by a path with `..` in it.
    const notes = resolvePath(REPORT, '../../../../README.md');
    await open(admin, '/invoices/catalog/import');
    await admin.evaluate(() =>
      document.querySelectorAll('input[type="file"]').forEach((i) => i.removeAttribute('accept'))
    );
    await chooseReport(admin, { characteristics: notes, prices: `${REPORT}prices.xlsx` });
    await submitted(
      admin,
      () => press(admin, `${ML_CARD} button[value="import"]`),
      'The characteristics file must be an .xlsx workbook.'
    );
    check(admin.url().endsWith('/invoices/catalog/import'), 'no run was started');
    return admin;
  }
);

await step(
  'V24',
  '-',
  'a channel with no importer is listed with its upload disabled, and why',
  async () => {
    await open(admin, '/invoices/catalog/import');
    const card = 'section[aria-label="Import from Tienda Centro"]';
    const state = await admin.$eval(card, (s: HTMLElement) => ({
      text: s.innerText,
      inputs: [...s.querySelectorAll('input, button')].map((e) => (e as HTMLInputElement).disabled)
    }));
    check(state.text.includes('Not available yet'), 'marked not available');
    check(
      state.text.includes('its report has to be analyzed and an importer written'),
      `says why: ${state.text}`
    );
    check(state.inputs.length >= 3 && state.inputs.every(Boolean), 'its upload is disabled');
    return admin;
  }
);

await step(
  'V25',
  '+',
  '"Email me the report" sends the report by email when the run finishes',
  async () => {
    const before = new Set(readdirSync(MAIL));
    await open(admin, '/invoices/catalog/import');
    await chooseReport(admin, {
      characteristics: `${REPORT}characteristics.xlsx`,
      prices: `${REPORT}prices.xlsx`
    });
    await press(admin, `${ML_CARD} input[name="notify"]`);
    await runFromCard(admin, 'Preview', 'Nothing was saved');
    await waitForText(admin, 'The report was emailed to', 30000);
    const sent = readdirSync(MAIL)
      .filter((f) => !before.has(f))
      .map((f) => readFileSync(`${MAIL}/${f}`, 'utf8'));
    const mine = sent.find((m) => m.includes('Subject: Catalog preview finished: Mercado Libre'));
    check(mine, `an email was written; saw ${sent.length}`);
    check(mine?.includes('To: admin@camille.test'), 'to the admin who started it');
    check(mine?.includes('Rejected rows: 33'), 'with the rejected rows');
    check(mine?.includes('/invoices/catalog/import/'), 'and a link to the run');
    return admin;
  }
);

await step('V26', '-', 'swapped files start a run that fails, saying why', async () => {
  await open(admin, '/invoices/catalog/import');
  await chooseReport(admin, {
    characteristics: `${REPORT}prices.xlsx`,
    prices: `${REPORT}characteristics.xlsx`
  });
  await runFromCard(admin, 'Import', 'Failed');
  check(
    (await bodyText(admin)).includes('The characteristics file has no category sheets.'),
    'the reason is shown'
  );
  return admin;
});

// Who sees what, and the phone ------------------------------------------------

await step('V16', '-', 'a member sees the catalog but no way to change it', async () => {
  const member = await signedIn(seed.member);
  await open(member, '/invoices/catalog');
  let text = await bodyText(member);
  check(text.includes(`${seed.products} products`), 'a member reads the list');
  check(!text.includes('New product'), 'no New product');
  await open(member, `/invoices/catalog/${classic}`);
  text = await bodyText(member);
  check(text.includes('in stock') && text.includes('MX$'), 'stock and prices are shown');
  check(!text.includes('Add a variant') && !text.includes('Delete product'), 'no edit controls');
  await open(member, '/invoices/catalog/categories');
  text = await bodyText(member);
  check(!text.includes('Add category'), 'no Add category');
  check(!text.includes('Import'), 'no Import tab');
  await open(member, '/invoices/catalog/channels');
  check(!(await bodyText(member)).includes('Add channel'), 'no Add channel');
  await open(member, '/invoices/catalog/import');
  text = await bodyText(member);
  check(
    text.includes('Only an administrator can import into the catalog.') &&
      !text.includes('Preview'),
    'the import page offers a member nothing'
  );
  return member;
});

await step(
  'V17',
  '+',
  'at phone width (390px) the pages fit without sideways scrolling or overlapping',
  async () => {
    const phone = await signedIn(seed.admin, 390);
    for (const path of [
      '/invoices/catalog',
      `/invoices/catalog/${classic}`,
      '/invoices/catalog/categories',
      '/invoices/catalog/channels',
      '/invoices/catalog/import'
    ]) {
      await open(phone, path);
      const wide = await phone.evaluate(() => document.documentElement.scrollWidth);
      check(wide <= 391, `${path} is ${wide}px wide`);
      // Rows become cards on a phone; no two cells of a card may cover each other.
      const clash = await phone.evaluate(() => {
        for (const row of document.querySelectorAll('table.v2-table tbody tr')) {
          // What each cell draws, not its box: a title cell keeps padding free
          // on the right for the card's status tag.
          const boxes = [...row.querySelectorAll(':scope > td')]
            .map((cell) => {
              const range = document.createRange();
              range.selectNodeContents(cell);
              return range.getBoundingClientRect();
            })
            .filter((box) => box.width > 0 && box.height > 0);
          for (let i = 0; i < boxes.length; i++) {
            for (let j = i + 1; j < boxes.length; j++) {
              const [a, b] = [boxes[i], boxes[j]];
              const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
              if (x > 1 && y > 1) return (row as HTMLElement).innerText.slice(0, 60);
            }
          }
        }
        return '';
      });
      check(!clash, `${path}: cells overlap in the row "${clash}"`);
    }
    await open(phone, `/invoices/catalog/${classic}`);
    await phone.evaluate(() => document.querySelector('table.v2-table')?.scrollIntoView());
    await phone.screenshot({ path: `${SHOTS}/V17-product.png`, fullPage: true });
    await open(phone, '/invoices/catalog');
    return phone;
  }
);

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
