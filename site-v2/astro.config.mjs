import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import tailwind from '@astrojs/tailwind';
import react from '@astrojs/react';

export default defineConfig({
  site: 'https://mycellm.dev',
  integrations: [
    starlight({
      title: 'mycellm_',
      logo: {
        dark: './src/assets/mycellm-lockup.svg',
        light: './src/assets/mycellm-lockup-light.svg',
        replacesTitle: true,
      },
      customCss: ['./src/styles/starlight.css'],
      sidebar: [
        { label: 'Quick Start', items: [
          { label: 'Installation', slug: 'quickstart/install' },
          { label: 'First Chat', slug: 'quickstart/chat' },
          { label: 'Join the Network', slug: 'quickstart/join' },
        ]},
        { label: 'Configuration', items: [
          { label: 'Settings', slug: 'config/settings' },
          { label: 'Secrets', slug: 'config/secrets' },
        ]},
        { label: 'API', items: [
          { label: 'Overview', slug: 'api/overview' },
          { label: 'Chat Completions', slug: 'api/chat' },
          { label: 'Models', slug: 'api/models' },
          { label: 'Public Gateway', slug: 'api/gateway' },
        ]},
        { label: 'Integrations', items: [
          { label: 'Relay Backends', slug: 'integrations/relay' },
          { label: 'OpenAI SDK', slug: 'integrations/openai' },
          { label: 'OpenCode', slug: 'integrations/opencode' },
          { label: 'OpenClaw', slug: 'integrations/openclaw' },
          { label: 'Claude Code', slug: 'integrations/claude-code' },
          { label: 'Docker', slug: 'integrations/docker' },
        ]},
        { label: 'CLI', items: [
          { label: 'Reference', slug: 'cli/reference' },
          { label: 'Chat REPL', slug: 'cli/chat' },
        ]},
        { label: 'Architecture', items: [
          { label: 'Overview', slug: 'architecture/overview' },
        ]},
      ],
    }),
    tailwind({ applyBaseStyles: false }),
    react(),
  ],
});
