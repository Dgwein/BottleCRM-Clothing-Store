import { error, fail, redirect } from '@sveltejs/kit';
import { apiStatus } from '#lib/api-errors.js';
import { catalogError, getImportRun, importPreviewed } from '#lib/server/v2/catalog.js';
import type { Actions, PageServerLoad } from './$types';

/** One import run: queued, running (the page refreshes itself), or its report. */
export const load: PageServerLoad = async (event) => {
  try {
    return { run: await getImportRun(event, event.params.id) };
  } catch (err) {
    const status = apiStatus(err);
    if (status === 404) error(404, 'That import is not here.');
    if (status === 403) error(403, 'Only an administrator can see imports.');
    throw err;
  }
};

export const actions: Actions = {
  /** Import the files a finished preview checked, and open the new run. */
  import: async (event) => {
    const form = await event.request.formData();
    let runId: string;
    try {
      runId = (await importPreviewed(event, event.params.id, form.get('notify') === 'on')).id;
    } catch (err) {
      const status = apiStatus(err);
      return fail(status === 409 || status === 403 ? status : 400, {
        error:
          status === 403
            ? 'Only an administrator can import into the catalog.'
            : catalogError(err, 'The files could not be imported.')
      });
    }
    redirect(303, `/invoices/catalog/import/${runId}`);
  }
};
