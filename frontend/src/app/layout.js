import "./globals.css";

export const metadata = {
  title: "MediBot | MediAssist Intelligent Assistant",
  description: "Sleek intelligent clinical and operational assistant for MediAssist Health Network with role-based access control.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="min-h-full flex flex-col" suppressHydrationWarning>{children}</body>
    </html>
  );
}
