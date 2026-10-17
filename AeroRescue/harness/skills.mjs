import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

/** The per-skill manifests are the application roster; no second copied list. */
export async function loadSkills(root) {
  const entries = await readdir(root, { withFileTypes: true });
  const skills = [];
  for (const entry of entries.filter(item => item.isDirectory()).sort((a, b) => a.name.localeCompare(b.name))) {
    const directory = join(root, entry.name);
    const manifest = JSON.parse(await readFile(join(directory, 'skill.json'), 'utf8'));
    const manual = await readFile(join(directory, 'SKILL.md'), 'utf8');
    skills.push({ ...manifest, directory, manual, toolName: manifest.name.replaceAll('-', '_') });
  }
  return skills;
}
