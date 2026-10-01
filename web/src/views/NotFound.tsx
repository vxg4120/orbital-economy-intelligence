import { Link, useLocation } from "react-router-dom";
import { EmptyState } from "../components/States";

/** Catch-all for unknown paths: the shell stays, the main plane says so and points back into the
    graph instead of rendering a void. */
export function NotFound() {
  const { pathname } = useLocation();
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
