import { fail } from '@sveltejs/kit';
import {
  addOption,
  attempt,
  createAttribute,
  deleteAttribute,
  deleteOption,
  DATA_TYPES,
  listAttributes,
  parseAttributeForm,
  parseOptionForm,
  renameOption,
  updateAttribute
} from '#lib/server/v2/catalog.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * The attributes the catalog describes products with, shared by every
 * category that uses them, with their options and the channel columns they
 * are loaded from. One in use is not deleted, and its type does not change.
 */
export const load: PageServerLoad = async (event) => ({
  ...(await listAttributes(event)),
  data_types: DATA_TYPES
});

const idOf = (form: FormData) => (form.get('id') ?? '').toString();

export const actions: Actions = {
  create: async (event) => {
    const parsed = parseAttributeForm(await event.request.formData(), { creating: true });
    if (!parsed.ok) return fail(400, { form: 'create', error: parsed.error });
    const refused = await attempt(
      () => createAttribute(event, parsed.payload),
      'Could not add the attribute.',
      'create'
    );
    return refused ?? { form: 'create', saved: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseAttributeForm(form, { creating: false });
    if (!parsed.ok) return fail(400, { form: `attribute:${id}`, error: parsed.error });
    const refused = await attempt(
      () => updateAttribute(event, id, parsed.payload),
      'Could not save the attribute.',
      `attribute:${id}`
    );
    return refused ?? { form: `attribute:${id}`, saved: true };
  },

  delete: async (event) => {
    const id = idOf(await event.request.formData());
    const refused = await attempt(
      () => deleteAttribute(event, id),
      'Could not delete the attribute.',
      `attribute:${id}`
    );
    return refused ?? { form: 'list', saved: true };
  },

  addOption: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseOptionForm(form);
    if (!parsed.ok) return fail(400, { form: `attribute:${id}`, error: parsed.error });
    const refused = await attempt(
      () => addOption(event, id, parsed.payload),
      'Could not add the option.',
      `attribute:${id}`
    );
    return refused ?? { form: `attribute:${id}`, saved: true };
  },

  renameOption: async (event) => {
    const form = await event.request.formData();
    const attribute = (form.get('attribute') ?? '').toString();
    const parsed = parseOptionForm(form);
    if (!parsed.ok) return fail(400, { form: `attribute:${attribute}`, error: parsed.error });
    const refused = await attempt(
      () => renameOption(event, idOf(form), parsed.payload),
      'Could not rename the option.',
      `attribute:${attribute}`
    );
    return refused ?? { form: `attribute:${attribute}`, saved: true };
  },

  deleteOption: async (event) => {
    const form = await event.request.formData();
    const attribute = (form.get('attribute') ?? '').toString();
    const refused = await attempt(
      () => deleteOption(event, idOf(form)),
      'Could not delete the option.',
      `attribute:${attribute}`
    );
    return refused ?? { form: `attribute:${attribute}`, saved: true };
  }
};
