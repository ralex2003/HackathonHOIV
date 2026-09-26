/**
 * Structured console logger for the Protein Friend Finder frontend.
 *
 * Provides consistent, labelled logging across all components and
 * services using console.group/console.info/console.warn/console.error.
 */

const PREFIX = "🧬 ProteinFriend";

/** Helper to create a labelled logger instance */
function createLogger(module) {
  return {
    /** Log an informational message */
    info: (message, ...args) => {
      console.info(`${PREFIX} [${module}] ℹ️ ${message}`, ...args);
    },

    /** Log a warning message */
    warn: (message, ...args) => {
      console.warn(`${PREFIX} [${module}] ⚠️ ${message}`, ...args);
    },

    /** Log an error message */
    error: (message, ...args) => {
      console.error(`${PREFIX} [${module}] ❌ ${message}`, ...args);
    },

    /** Log a debug message (visible in dev mode) */
    debug: (message, ...args) => {
      console.debug(`${PREFIX} [${module}] 🔍 ${message}`, ...args);
    },

    /** Log a grouped set of messages */
    group: (label, fn) => {
      console.group(`${PREFIX} [${module}] ${label}`);
      try {
        fn();
      } finally {
        console.groupEnd();
      }
    },
  };
}

// Module-specific loggers
export const apiLogger = createLogger("API");
export const searchLogger = createLogger("SearchBar");
export const graphLogger = createLogger("GraphViz");
export const proteinLogger = createLogger("ProteinPanel");
export const paperLogger = createLogger("PaperPanel");
export const filterLogger = createLogger("Filters");
export const appLogger = createLogger("App");
export const contextLogger = createLogger("AppContext");

export default createLogger;
