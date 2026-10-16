import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { EmptyState } from "../components/States";

/** Catch-all for unknown paths: the shell stays, the main plane says so and points back into the
    graph instead of rendering a void. */
export function NotFound() {
  const { pathname } = useLocation();
  // browser history and tabs should say the link was dead; no other view sets the title,
  // so the previous one comes back on the way out
  useEffect(() => {
    const prev = document.title;
    document.title = "Page not found · Orbital Economy Terminal";
    return () => {
      document.title = prev;
    };
  }, []);
  return (
    <div className="view fadein">
      <EmptyState title="Page not found" message={`Nothing resolves at ${pathname}.`} />
      <div className="inline" style={{ justifyContent: "center" }}>
        <Link to="/" className="btn">
          Overview
        </Link>
        <Link to="/resolver" className="btn">
          Resolver
        </Link>
      </div>
    </div>
  );
}
