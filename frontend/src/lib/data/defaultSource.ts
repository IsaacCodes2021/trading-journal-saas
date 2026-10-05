/*
 * The source pages read from when nothing else is provided: the sample journal. This is what `pnpm dev` (without
 * `TJ_DEV_API=real`) and the tests use. A build does not: vite.config.ts swaps this module for `defaultSource.live.ts`, so
 * no part of the build imports the sample journal statically, and it can be loaded later, only when someone asks for the
 * demo (components/DataSourceRoot.tsx). Both files must export the same name.
 */
export { demoSource as defaultSource } from '../demo/source'
