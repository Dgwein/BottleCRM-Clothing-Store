/** How an import run's status reads, and its pill's tone. */
export const RUN_STATUS: Record<
  string,
  { label: string; tone: 'slate' | 'ink' | 'moss' | 'rust' }
> = {
  QUEUED: { label: 'Queued', tone: 'slate' },
  RUNNING: { label: 'Running', tone: 'ink' },
  DONE: { label: 'Done', tone: 'moss' },
  FAILED: { label: 'Failed', tone: 'rust' }
};
