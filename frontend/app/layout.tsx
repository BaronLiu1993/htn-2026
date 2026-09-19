import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "UnderwriteIQ | Federato submission intelligence",
  description:
    "A schema-aware underwriting triage agent for Federato commercial property submissions.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
