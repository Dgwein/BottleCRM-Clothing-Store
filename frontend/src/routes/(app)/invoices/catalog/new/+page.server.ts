import { error, fail, redirect } from '@sveltejs/kit';
import {
  attempt,
  createProduct,
  listCategories,
  parseProductForm
} from '#lib/server/v2/catalog.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * A new catalog product: its name, category and description. Characteristics
 * and variants come next, on the product's page, because which of them
 * apply depends on the category.
 */
export const load: PageServerLoad = async (event) => {
  const { categories, can_manage } = await listCategories(event);
  if (!can_manage) error(403, 'Only an administrator can change the catalog.');
  return { categories: categories.map((c) => ({ id: c.id, name: c.name })) };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const values = {
      name: (form.get('name') ?? '').toString(),
      category: (form.get('category') ?? '').toString(),
      description: (form.get('description') ?? '').toString()
    };
    const parsed = parseProductForm(form, { creating: true });
    if (!parsed.ok) return fail(400, { form: 'create', error: parsed.error, values });
    let id = '';
    const refused = await attempt(
      async () => {
        id = (await createProduct(event, parsed.payload)).id;
      },
      'Could not add the product.',
      'create'
    );
    if (refused) return fail(refused.status, { ...refused.data, values });
    redirect(303, `/invoices/catalog/${id}`);
  }
};
