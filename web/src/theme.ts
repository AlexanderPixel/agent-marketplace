import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

/** Aura with an EU-blue accent. */
export const AppTheme = definePreset(Aura, {
  primitive: { borderRadius: { sm: '6px', md: '8px', lg: '10px', xl: '12px' } },
  semantic: {
    primary: {
      50: '#eef3fb', 100: '#d6e2f6', 200: '#adc4ec', 300: '#7a9fde', 400: '#4a78cc',
      500: '#2a59b5', 600: '#1d4596', 700: '#173777', 800: '#132d60', 900: '#0f244c', 950: '#091630',
    },
    colorScheme: {
      light: {
        primary: { color: '{primary.600}', contrastColor: '#ffffff', hoverColor: '{primary.700}', activeColor: '{primary.800}' },
      },
    },
  },
})
