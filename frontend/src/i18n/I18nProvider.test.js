import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import { I18nProvider, useI18n } from './I18nProvider';

function LocaleProbe() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div>
      <span data-testid="locale-value">{locale}</span>
      <span>{t('viewerTitle')}</span>
      <button type="button" onClick={() => setLocale('zh-CN')}>
        switch-zh
      </button>
    </div>
  );
}

test('I18nProvider switches between english and chinese messages', () => {
  window.localStorage.clear();

  render(
    <I18nProvider>
      <LocaleProbe />
    </I18nProvider>
  );

  expect(screen.getByText('Journal viewer')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'switch-zh' }));
  expect(screen.getByText('提交回放')).toBeInTheDocument();
  expect(screen.getByTestId('locale-value')).toHaveTextContent('zh-CN');
});
