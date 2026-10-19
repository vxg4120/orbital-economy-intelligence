import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { applyRouteMeta } from "../seo";

/** Renders nothing; keeps the document head in step with the route. Sits inside the router so
 *  every navigation, including the catch-all, lands on the right title, description, canonical
 *  URL and robots directive. */
export function RouteMeta() {
  const { pathname } = useLocation();
  useEffect(() => {
    applyRouteMeta(pathname);
  }, [pathname]);
  return null;
}
