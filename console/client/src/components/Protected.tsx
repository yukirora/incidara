import { type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useMe } from "../hooks/useMe.ts";

interface ProtectedProps {
  children: ReactNode;
}

export function Protected({ children }: ProtectedProps) {
  const { user, isLoading } = useMe();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <span className="text-gray-500">Loading...</span>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
