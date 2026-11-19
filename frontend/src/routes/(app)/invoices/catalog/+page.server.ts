import { listCatalog, pageQuery, readCatalogFilters, PAGE_SIZE } from '#lib/server/v2/catalog.js';
import type { PageServerLoad } from './$types';

/**
 * Catalog products, one page at a time, narrowed by name, category, size and
 * stock. A size or stock filter matches a product when one of its variants
 * does. The previous and next page links keep the filters.
 */
export const load: PageServerLoad = async (event) => {
  const filters = readCatalogFilters(event.url);
  const { results, count, categories, sizes, can_manage } = await listCatalog(event, filters);
  const to = filters.offset + results.length;
  return {
    products: results,
    count,
    categories,
    sizes,
    filters,
    can_manage,
    from: count === 0 ? 0 : filters.offset + 1,
    to,
    prev: filters.offset > 0 ? pageQuery(filters, Math.max(0, filters.offset - PAGE_SIZE)) : null,
    next: to < count ? pageQuery(filters, filters.offset + PAGE_SIZE) : null
  };
};
