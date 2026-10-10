import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// Wait for observable UI state, allowing scheduling delays on shared hosts.
configure({ asyncUtilTimeout: 5_000 });
