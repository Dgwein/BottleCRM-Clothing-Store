import { describe, it, expect } from 'vitest';
import { visibleTabs } from '#lib/v2/tabs.js';

const row = (key: string, available: boolean) => ({
  key,
  label: key,
  group: 'bill' as const,
  covers: '',
  stops: '',
  enabled: available,
  available,
  needs: null
});
const modules = (off: string[]) =>
  ['deals', 'tasks', 'tickets', 'invoices'].map((k) => row(k, !off.includes(k)));
const labels = (tabs: any[]) => tabs.map((t) => t.label);

describe('visibleTabs', () => {
  it('keeps Catalog and Products while invoices or deals is on, and hides admin tabs from members', () => {
    const only = (off: string[]) =>
      labels(visibleTabs('invoices', { isAdmin: false, modules: modules(off) }));
    expect(only(['invoices'])).toEqual(['Catalog', 'Products']);
    expect(only(['deals'])).toContain('Products');
    expect(only(['deals'])).toContain('Catalog');
    expect(only(['invoices', 'deals'])).toEqual([]);
    expect(only([])).not.toContain('Reports');
    expect(labels(visibleTabs('invoices', { isAdmin: true, modules: null }))).toContain('Reports');
    const catalog = (isAdmin: boolean) =>
      labels(visibleTabs('catalog', { isAdmin, modules: modules([]) }));
    expect(catalog(true)).toEqual([
      'Products',
      'Categories',
      'Attributes',
      'Sales channels',
      'Import'
    ]);
    expect(catalog(false)).not.toContain('Import');
  });

  it('drops a set whose module is off and keeps everything for a null list', () => {
    expect(visibleTabs('tickets', { isAdmin: true, modules: modules(['tickets']) })).toEqual([]);
    expect(visibleTabs('tasks', { modules: null })).toHaveLength(3);
  });
});
