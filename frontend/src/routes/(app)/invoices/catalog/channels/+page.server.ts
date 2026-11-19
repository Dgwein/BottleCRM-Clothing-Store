import { fail } from '@sveltejs/kit';
import {
  attempt,
  CHANNEL_KINDS,
  createChannel,
  deleteChannel,
  listChannels,
  parseChannelForm,
  updateChannel
} from '#lib/server/v2/catalog.js';
import type { Actions, PageServerLoad } from './$types';

/**
 * Where the store sells: online channels (Mercado Libre, Amazon, its own web
 * shop) and physical stores. Every channel sells the whole catalog; an
 * invoice records the one a sale came through. A channel with listings or
 * sales is not deleted: switch it off instead.
 */
export const load: PageServerLoad = async (event) => ({
  ...(await listChannels(event)),
  kinds: CHANNEL_KINDS
});

const idOf = (form: FormData) => (form.get('id') ?? '').toString();

export const actions: Actions = {
  create: async (event) => {
    const parsed = parseChannelForm(await event.request.formData(), { creating: true });
    if (!parsed.ok) return fail(400, { form: 'create', error: parsed.error });
    const refused = await attempt(
      () => createChannel(event, parsed.payload),
      'Could not add the sales channel.',
      'create'
    );
    return refused ?? { form: 'create', saved: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const id = idOf(form);
    const parsed = parseChannelForm(form, { creating: false });
    if (!parsed.ok) return fail(400, { form: `channel:${id}`, error: parsed.error });
    const refused = await attempt(
      () => updateChannel(event, id, parsed.payload),
      'Could not save the sales channel.',
      `channel:${id}`
    );
    return refused ?? { form: `channel:${id}`, saved: true };
  },

  delete: async (event) => {
    const id = idOf(await event.request.formData());
    const refused = await attempt(
      () => deleteChannel(event, id),
      'Could not delete the sales channel.',
      `channel:${id}`
    );
    return refused ?? { form: 'list', saved: true };
  }
};
