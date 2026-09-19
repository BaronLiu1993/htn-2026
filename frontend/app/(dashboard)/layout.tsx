import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./shell.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "UnderwriteIQ | Federato submission intelligence",
  description:
    "A schema-aware underwriting triage agent for Federato commercial property submissions.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
