import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

/** Aura with the EU Patriot look: signal blue on graphite, square corners. */
export const AppTheme = definePreset(Aura, {
  primitive: { borderRadius: { none: '0', xs: '0', sm: '0', md: '0', lg: '0', xl: '0' } },
  semantic: {
    primary: {
      50: '#eef3fe', 100: '#d9e4fd', 200: '#b3c8fb', 300: '#82a5f6', 400: '#4f7ef0',
      500: '#1957ea', 600: '#1449c9', 700: '#123ca3', 800: '#11327f', 900: '#0f2a66', 950: '#0a1a40',
    },
    colorScheme: {
      light: {
        primary: { color: '{primary.500}', contrastColor: '#ffffff', hoverColor: '{primary.600}', activeColor: '{primary.700}' },
        surface: {
          0: '#ffffff', 50: '#f6f6f6', 100: '#ecedee', 200: '#dcddde', 300: '#c8cacc', 400: '#a9aeb1',
          500: '#7d8286', 600: '#5b6064', 700: '#3a4044', 800: '#232a2e', 900: '#12171a', 950: '#0a0d0f',
        },
      },
    },
  },
})
