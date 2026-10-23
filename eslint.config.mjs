import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['var/**', 'node_modules/**'] },
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ['apps/**/*.ts', 'packages/**/*.ts', 'plugins/**/*.ts'],
    languageOptions: {
      parserOptions: { project: './tsconfig.json', tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      'no-restricted-imports': [
        'error',
        { patterns: ['**/apps/**', '**/plugins/**', '**/packages/*/src/**'] },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/consistent-type-assertions': ['error', { assertionStyle: 'never' }],
    },
  },
  {
    files: ['packages/contracts/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            '@game-automation/plugin-sdk*',
            '@game-automation/control-plane*',
            '@game-automation/agent-codex*',
            '**/apps/**',
            '**/plugins/**',
          ],
        },
      ],
    },
  },
  {
    files: ['packages/plugin-sdk/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            '@game-automation/control-plane*',
            '@game-automation/agent-codex*',
            '**/apps/**',
            '**/plugins/**',
          ],
        },
      ],
    },
  },
);
