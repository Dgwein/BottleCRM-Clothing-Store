import { fail, redirect } from '@sveltejs/kit';
import { apiStatus } from '#lib/api-errors.js';
import {
  catalogError,
  listImportRuns,
  listImportSources,
  parseImportForm,
  startImport
} from '#lib/server/v2/catalog.js';
import { viewerIsAdmin } from '#lib/server/v2/organization.js';
import type { Actions, PageServerLoad } from './$types';

const ADMIN_ONLY = 'Only an administrator can import into the catalog.';

/**
 * Import a channel's report into the catalog. Only sources with an importer
 * take files (Mercado Libre); every other channel is listed, disabled, until
 * its report has been analyzed and an importer written. A run goes to the
 * background at once and its page follows it; the report can be emailed.
 * The API decides who may import (an admin), checks the files again,
 * throttles, and writes only to the caller's org.
 */
export const load: PageServerLoad = async (event) => {
  if (!viewerIsAdmin(event.cookies)) return { can_manage: false, sources: [], runs: [] };
  const [sources, runs] = await Promise.all([listImportSources(event), listImportRuns(event)]);
  return { can_manage: true, sources, runs };
};

export const actions: Actions = {
  start: async (event) => {
    const form = await event.request.formData();
    const code = (form.get('source') ?? '').toString();
    let sources;
    try {
      sources = await listImportSources(event);
    } catch (err) {
      if (apiStatus(err) !== 403) throw err;
      return fail(403, { source: code, error: ADMIN_ONLY });
    }
    const source = sources.find((s) => s.code === code);
    if (!source) return fail(400, { source: code, error: 'Choose what to import.' });
    const parsed = parseImportForm(form, source);
    if (!parsed.ok) return fail(400, { source: code, error: parsed.error });
    let runId: string;
    try {
      runId = (await startImport(event, parsed.payload)).id;
    } catch (err) {
      const status = apiStatus(err);
      const error =
        status === 403
          ? ADMIN_ONLY
          : status === 429
            ? 'Too many imports in a minute. Wait a moment, then try again.'
            : catalogError(err, 'The report could not be sent.');
      return fail(status === 409 || status === 403 || status === 429 ? status : 400, {
        source: code,
        error
      });
    }
    redirect(303, `/invoices/catalog/import/${runId}`);
  }
};
