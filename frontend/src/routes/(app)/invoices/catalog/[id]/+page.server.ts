import { error, fail, redirect } from '@sveltejs/kit';
import {
  addVariant,
  attempt,
  deleteProduct,
  deleteVariant,
  getCatalogProduct,
  parseCharacteristics,
  parseProductForm,
  parseVariantForm,
  parseVariantRow,
  updateProduct,
  updateVariant
} from '#lib/server/v2/catalog.js';
import { apiStatus } from '#lib/api-errors.js';
import type { Actions, PageServerLoad, RequestEvent } from './$types';

const idOf = (form: FormData) => (form.get('id') ?? '').toString();

/**
 * One catalog product with its characteristics, variants and listings, and
 * which characteristics and axes its category has, for the forms.
 */
async function current(event: RequestEvent) {
  try {
    return await getCatalogProduct(event, event.params.id);
  } catch (err) {
    if (apiStatus(err) === 404) error(404, 'Product not found');
    throw err;
  }
}

export const load: PageServerLoad = (event) => current(event);

export const actions: Actions = {
  /** Name, description and whether the product sells. */
  product: async (event) => {
    const parsed = parseProductForm(await event.request.formData(), { creating: false });
    if (!parsed.ok) return fail(400, { form: 'product', error: parsed.error });
    const refused = await attempt(
      () => updateProduct(event, event.params.id, parsed.payload),
      'Could not save the product.',
      'product'
    );
    return refused ?? { form: 'product', saved: true };
  },

  /** What the product is: one value per characteristic, or none. */
  characteristics: async (event) => {
    const form = await event.request.formData();
    const { characteristics } = await current(event);
    const parsed = parseCharacteristics(form, characteristics);
    if (!parsed.ok) return fail(400, { form: 'characteristics', error: parsed.error });
    const refused = await attempt(
      () => updateProduct(event, event.params.id, { attributes: parsed.payload }),
      'Could not save the characteristics.',
      'characteristics'
    );
    return refused ?? { form: 'characteristics', saved: true };
  },

  /** A new variant: its size, stock, price and axes. */
  addVariant: async (event) => {
    const form = await event.request.formData();
    const { axis_fields } = await current(event);
    const parsed = parseVariantForm(form, axis_fields);
    if (!parsed.ok) return fail(400, { form: 'addVariant', error: parsed.error });
    const refused = await attempt(
      () => addVariant(event, event.params.id, parsed.payload),
      'Could not add the variant.',
      'addVariant'
    );
    return refused ?? { form: 'addVariant', saved: true };
  },

  /** One variant's price, stock, size and whether it sells. */
  variant: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseVariantRow(form);
    if (!parsed.ok) return fail(400, { form: `variant:${id}`, error: parsed.error });
    const refused = await attempt(
      () => updateVariant(event, id, parsed.payload),
      'Could not save this variant.',
      `variant:${id}`
    );
    return refused ?? { form: `variant:${id}`, saved: true };
  },

  /** Refused when a deal, order or invoice sells the variant. */
  deleteVariant: async (event) => {
    const id = idOf(await event.request.formData());
    const refused = await attempt(
      () => deleteVariant(event, id),
      'Could not delete this variant.',
      `variant:${id}`
    );
    return refused ?? { form: 'variants', saved: true };
  },

  /** Refused when a deal, order or invoice sells one of its variants. */
  delete: async (event) => {
    const refused = await attempt(
      () => deleteProduct(event, event.params.id),
      'Could not delete the product.',
      'delete'
    );
    if (refused) return refused;
    redirect(303, '/invoices/catalog');
  }
};
