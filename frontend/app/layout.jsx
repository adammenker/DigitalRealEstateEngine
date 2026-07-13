import "./globals.css";

export const metadata = {
  title: "Digital Real Estate Engine",
  description: "Local opportunity scanning dashboard for rank-and-rent research."
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

