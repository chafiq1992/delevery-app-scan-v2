# Chat-style Timeline for Order Notes

This document outlines the new chat-style layout for the "Today Suivi" tab on the Follow page.

## 1. Chat layout
- All events (scans, status changes, driver notes and agent notes) appear as separate bubbles in a vertical stream, newest at the bottom.
- System events such as status updates align on the left. Driver and agent messages align on the right.

## 2. Bubble details
- Each bubble shows a small icon, a short label (e.g. "Dispatched", "Driver note", "Agent call") and a timestamp in light gray.
- Colors help differentiate events: blue for system status, green for driver messages and gray for agent messages.

## 3. Date separators
- When the date changes, the timeline inserts a centred divider, such as `— Mon 15 Jul —`, similar to WhatsApp.

## 4. Input area
- When the user expands the note section, the text area stays anchored at the bottom of the card, like a chat reply box.
- Pressing the **Send** button immediately adds the new bubble and scrolls the view to the bottom.

## 5. Scrolling and history
- Opening a card automatically scrolls to the newest bubble while allowing scrolling back to older notes.
- If the user scrolls away from the bottom, a subtle “scroll to bottom” arrow appears.

## 6. Visual polish
- Bubbles have rounded 12&nbsp;px corners and a light drop shadow.
- Maximum bubble width is about 75&nbsp;% of the card so the left or right alignment is clear.
- Timestamp text uses a smaller font size (about 0.8&nbsp;em) while the rest of the font matches the rest of the app.

## 7. System highlights
- A delivery status of **Returned** or **Cancelled** shows a light red background.
- A **Delivered** status shows a light green background.

This chat-style timeline lets the follow-up agent read the entire order history quickly, just like a messaging app conversation.
