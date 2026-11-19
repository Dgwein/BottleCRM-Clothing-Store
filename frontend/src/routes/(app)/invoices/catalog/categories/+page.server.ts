import { fail } from '@sveltejs/kit';
import {
  attempt,
  createCategory,
  deleteCategory,
  linkAttribute,
  listCategories,
  parseCategoryForm,
  parseLinkForm,
  renameCategory,
  unlinkAttribute
} from '#lib/server/v2/catalog.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * The categories, each with its product count and the attributes it uses:
 * its variant axes (besides size, which every variant has) and its
 * characteristics. Once a category has variants its axes are fixed; the API
 * refuses the change and the page says why.
 */
export const load: PageServerLoad = (event) => listCategories(event);

const idOf = (form: FormData) => (form.get('id') ?? '').toString();

export const actions: Actions = {
  create: async (event) => {
    const parsed = parseCategoryForm(await event.request.formData());
    if (!parsed.ok) return fail(400, { form: 'create', error: parsed.error });
    const refused = await attempt(
      () => createCategory(event, parsed.payload),
      'Could not add the category.',
      'create'
    );
    return refused ?? { form: 'create', saved: true };
  },

  rename: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseCategoryForm(form);
    if (!parsed.ok) return fail(400, { form: `category:${id}`, error: parsed.error });
    const refused = await attempt(
      () => renameCategory(event, id, parsed.payload),
      'Could not rename the category.',
      `category:${id}`
    );
    return refused ?? { form: `category:${id}`, saved: true };
  },

  delete: async (event) => {
    const id = idOf(await event.request.formData());
    const refused = await attempt(
      () => deleteCategory(event, id),
      'Could not delete the category.',
      `category:${id}`
    );
    return refused ?? { form: 'list', saved: true };
  },

  link: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseLinkForm(form);
    if (!parsed.ok) return fail(400, { form: `category:${id}`, error: parsed.error });
    const refused = await attempt(
      () => linkAttribute(event, id, parsed.payload),
      'Could not add the attribute to the category.',
      `category:${id}`
    );
    return refused ?? { form: `category:${id}`, saved: true };
  },

  unlink: async (event) => {
    const form = await event.request.formData();
    const category = (form.get('category') ?? '').toString();
    const refused = await attempt(
      () => unlinkAttribute(event, idOf(form)),
      'Could not take the attribute off the category.',
      `category:${category}`
    );
    return refused ?? { form: `category:${category}`, saved: true };
  }
};
