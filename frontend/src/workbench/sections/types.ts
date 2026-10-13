import type { AlgorithmDescriptor } from '../../newcomers/api';
import type { StudioApi } from '../../studio/api';
import type { useI18n } from '../../i18n/I18nProvider';
import type { Project, SectionId, ValidationIssue, WorkbenchApi } from '../api';

export type Scenario = Record<string, unknown>;

/** What every configuration section receives from ConfigPanel. Sections edit the shared draft only through `edit`/`replaceDraft`. */
export interface SectionProps {
  api: WorkbenchApi; studioApi: StudioApi; project: Project;
  draft: Scenario; edit: (path: string, value: unknown) => void; replaceDraft: (scenario: Scenario) => void;
  focus?: string; zh: boolean; locale: ReturnType<typeof useI18n>['locale']; L: (en: string, zh: string) => string;
  issues: ValidationIssue[]; onProject: (project: Project) => void; saveDraft: () => Promise<Project | undefined>;
  reportValidity: (path: string, valid: boolean) => void; onInspectContract?: (typeId: string, field?: string) => void;
  algorithms?: AlgorithmDescriptor[];
  /** Jump to another section without unmounting existing forms. `focus` is passed to that section,
   * e.g. goTo('objects', 'add-place') for its Add place flow or a JSON pointer for an authored field. */
  goTo?: (section: SectionId, focus?: string) => void;
}
