/** Subscribe a component to the UI language, so a switch re-renders the tree. */
import { useSyncExternalStore } from "react";

import { getLang, subscribe, type Lang } from "../i18n";

export function useLang(): Lang {
  // useSyncExternalStore rather than useState+useEffect: the language can change
  // between render and effect (the switch is a plain function call from anywhere),
  // and this is the API that reads a mutable external value without tearing.
  return useSyncExternalStore(subscribe, getLang, getLang);
}
