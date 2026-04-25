import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AuthShell } from "@/components/auth-shell";
import { ErrorBoundary } from "@/components/error-boundary";
import { ThemeProvider, themeInitScript } from "@/lib/theme";
import { WebcamPublisherProvider } from "@/lib/webcam-publisher";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Nurby",
  description: "AI camera monitoring platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <ThemeProvider>
          <AuthShell>
            <WebcamPublisherProvider>
              <ErrorBoundary>
                {children}
              </ErrorBoundary>
            </WebcamPublisherProvider>
          </AuthShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
