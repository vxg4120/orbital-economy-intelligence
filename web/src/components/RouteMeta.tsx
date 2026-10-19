import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { applyRouteMeta } from "../seo";

/** Renders nothing; keeps the document head in step with the location. Sits inside the router so
 *  every navigation, including the catch-all, lands on the right title, description, canonical
 *  URL and robots directive. The search string matters because some routes canonicalise on a
 *  query parameter (a bus slug can name a manufacturer or a bus model). */
export function RouteMeta() {
  const { pathname, search } = useLocation();
  useEffect(() => {
    applyRouteMeta(pathname, search);
  }, [pathname, search]);
  return null;
}
