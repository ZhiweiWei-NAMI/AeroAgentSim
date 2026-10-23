import { useEffect, useState } from 'react';
import type { OperationCopy } from './api';

/** Observe optional copy without delaying the plan or cancelling its shared request. */
export function useOperationCopy(copy: OperationCopy | undefined): OperationCopy | undefined {
  const [settled, setSettled] = useState<{ source: OperationCopy; value: OperationCopy }>();
  useEffect(() => {
    if (!copy?.subscribe) return;
    return copy.subscribe(value => setSettled({ source: copy, value }));
  }, [copy]);
  return settled?.source === copy ? settled?.value : copy;
}
