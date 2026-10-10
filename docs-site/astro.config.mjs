// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://bekas.ragilhadi.com',
  integrations: [
    sitemap(),
    starlight({
      title: 'Docs | bekas',
      description:
        'Audit your machine for forgotten stuff — orphaned Docker images, old node_modules, stale caches — and safely reclaim disk space with quarantine and undo.',
      favicon: '/favicon.svg',
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#1a1a2e' } },
        { tag: 'meta', attrs: { name: 'author', content: 'Ragil Ilman' } },
        { tag: 'link', attrs: { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' } },
        { tag: 'meta', attrs: { property: 'og:type', content: 'website' } },
        { tag: 'meta', attrs: { property: 'og:site_name', content: 'bekas' } },
        { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary' } },
        { tag: 'meta', attrs: { name: 'twitter:title', content: 'bekas — Reclaim disk space safely' } },
      ],
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/ragilhadi/bekas' },
        { icon: 'seti:python', label: 'PyPI', href: 'https://pypi.org/project/bekas/' },
      ],
      editLink: {
        baseUrl: 'https://github.com/ragilhadi/bekas/edit/master/docs-site/',
      },
      sidebar: [
        {
          label: 'Start Here',
          items: [
            { label: 'Introduction', slug: 'start-here/introduction' },
            { label: 'Installation', slug: 'start-here/installation' },
            { label: 'Quick Start', slug: 'start-here/quick-start' },
          ],
        },
        {
          label: 'Guides',
          items: [
            { label: 'Auditing', slug: 'guides/auditing' },
            { label: 'Planning & Cleaning', slug: 'guides/cleaning' },
            { label: 'Quarantine, History & Undo', slug: 'guides/quarantine-undo' },
            { label: 'Safety Model', slug: 'guides/safety' },
            { label: 'Configuration', slug: 'guides/configuration' },
            { label: 'Using the TUI', slug: 'guides/tui' },
            { label: 'Running in CI', slug: 'guides/ci' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Commands', slug: 'reference/commands' },
            { label: 'Plugins', slug: 'reference/plugins' },
            { label: 'Writing a Plugin', slug: 'reference/writing-plugins' },
            { label: 'Files & Locations', slug: 'reference/files' },
            { label: 'Troubleshooting', slug: 'reference/troubleshooting' },
          ],
        },
      ],
    }),
  ],
});
