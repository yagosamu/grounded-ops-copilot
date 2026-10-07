import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "GroundedOps | Evidence-first operations",
  description: "Engineering knowledge with verifiable evidence and explicit uncertainty.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
