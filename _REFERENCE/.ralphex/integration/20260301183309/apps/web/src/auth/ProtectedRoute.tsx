import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';

export function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
        <p className="rounded-md border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
          Checking session...
        </p>
      </main>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
