# Meditation Session Timer App – Requirements Specification

## 1. App Purpose and Technologial Stack
The app is a meditation manager Mainly for Android, designed to let users create, customize, and track their meditation sessions. It provides visual, acoustic, and organizational support throughout the meditation process.

### 🔧 Technology Stack & Infrastructure


### 1. **Frontend Framework**

* **Expo (with TypeScript)** — via Expo Go: all the libraries used must be compatible with Expo Go
* **State Management:** Zustand
* **UI Libraries:**
  * **React Native Paper** — for styled components
  * **React Navigation** — for screen routing and navigation

* **SQLite via `expo-sqlite`**
  * Used for all local database needs, fully compatible with Expo Go.
* **Daily Local Backup**
  * Each day, the SQLite DB file is copied using `expo-file-system` to an app-specific storage folder.
  * Backup metadata (paths, timestamps) can be tracked with `expo-secure-store`.

* **Notifications:** `expo-notifications`
  * Provides local and push notifications, including daily scheduled reminders (compatible with Expo Go for local scheduling).

* **Audio Playback:** `expo-av`
  * Handles audio playback (including background audio in managed builds), volume control, repeat, and local or streamed sources.

* **Image/File Picker:** `expo-image-picker`
  * For selecting images and files from device gallery or camera (works in Expo Go).

* **Image Caching:** `expo-image` or community `expo-fast-image`
  * For efficient image display and caching. Use Expo-friendly packages for compatibility.

* **File Management:** `expo-file-system`
  * Manages local files, including user-uploaded audio, DB backups, and cached media files.

* **Charts & Visualization:** `react-native-chart-kit`
  * Provides charting and visualization features (fully compatible with Expo Go).

* **Sharing:** `react-native-share`
  * Enables sharing stats and diary entries through the system share dialog.

* **Automated Background Jobs:**
  * Use `expo-background-fetch` and `expo-task-manager` for background and periodic/scheduled jobs in Expo Go.

* **Other Utilities:**
  * **`@react-native-picker/picker`** — for dropdown pickers (Expo Go compatible)
  * **`expo-keep-awake`** — prevents screen sleep during sessions
  * **`react-native-do-not-disturb`** — basic support in Expo Go for controlling DND mode (verify feature needs)
  * **`@react-navigation/native`** — navigation core (Expo Go compatible)
  * **`@react-native-async-storage/async-storage`** — for persistent key-value storage
  * expo-calendar Provides access to the system's built-in calendar and events/reminders


## 2. Data Structure:
The main piece of data of the application is the Session Timer.
A Session Timer is a sort of blueprint that descroibes how the session will work.
  - Session Timer Id (PK)
  - Session Timer name
  - Preparation time (seconds or minutes before the session Session Timer starts)
  - Segmentation Sound
    - Selectable from the system ringtones library
  - Segmentation Sound Repetition : 1 to 3 times
  - Segmentation Sound Volume
  - Meditation Sound: String
  - Meditation Sound Origin
    - String Selectable from:
      - system ringtones library
      - User's own audio files
  - Meditation Sound Repetition*
    - The Meditation Sound can be repeated forever or a selectable number of times
  - Meditation Sound Volume
  - Meditation segmentation: this is a string that will describe in what temporal segments the session is divided
  - Daily reminders Enabled
  - Enable Diary Note

- Diary: the diary is a way to add notes inside the application.
  - Creation Timestamp (PK)
  - Content

- Meditations Log:
  - Timestamp (PK): Time the meditation started
  - Actual Duration of the Meditation Performed

- System Notifications:
  - Notification Id (PK)
  - Notification Frequency (every n hours/days)
  - Notification Time (every n days at...)
  - Session Timer Id

- Settings
  - theme - Application Theme
  - adsFreePurchased - A flag that tells if the user paid for eliminating the ads
  - dndEnabled
  - backupEnabled
  - keepScreenOn
  - CountUp

## 2. Screens

### 2.1 Main Screen
- Upon launch, a list of existing Session Timers is displayed as a scrollable vertical list of items. Each existing Session Timer is rendered as a rounded edge rect box that displays/contains:
    - Duration (bold Large)
    - Preparation Time
    - Right Aligned Edit Button
    - Left Aligned Play Button
- On the Bottom-right of the screen a “+” floating button allows to create a new Session Timer.
- On the Bottom-Left of the screen a "Calendar" button allows you to Visualize the Calendar View
- Top-right: settings icon. Upon click a left side sliding "Settings" menu appears

### 2.2 Calendar View
- use react-native-calendars
- Uses a scrollable vertical list of items to display:
  - Meditations performed
    - Ordered by meditation date
    - Upon clicking on a meditation performed displayed as an entry in the calendar, a toaster appears displaying the performed meditation data (start date + time, Duration)
    - On the immediate right there will be a button to edit the Duration.
    - A button on the far right allows to create a Diary Entry (upon creation will have the same time stamp as the Meditation Performed)
    - If a Diary Entry was already created with the same Time Stamp as that of a meditation performed it will show it for edit
    - Within each item in the list you can have:
      - Diary entries
        - Only Diary Entries that have time stamps different from any meditation performed meditations will be shown as entries in the calendar
      - Diary View Button: On the top right corner a button allow to access the Diary View Page
      - A Back button on the top left side of the view to go back to the main page

### 2.3 Diary Entry editor
  - This window is used to edit/add a note in the diary
  - Undo/redo functionality buttons must be available at the bottom of the screen (maintain memory of the changes performed only while the window is open on a particular note. Exiting the Note Editing window the undo/redo data is wiped away)
  - A note that didn't existed before is created only after the user exits the editing screen at creation time
  - A cancel button allows to return to the initial version of the note and close the editing/discard the new note being edited
  - A Save Button allows to save and close the window.
  - A top Left Arrow Button works as a save button
  - A delete button to delete the note. Upon clicking a popup will appear asking if the useer is sure. UIpon Confirmation the editor will be closed, the entry deleted.

### 2.3 Meditation Session Window
  - Upon Activating a session (by clicking one of the meditation buttons in the main page) the window will appear (State = ready).
  - The window will contain the following elements:
    - A large round timer in the center of the page. This will display the expired time of each part of the programmed time for the session (Preparation time and then the time for each segment of the session).
    At the beginning of each part (preparation or segment) the round timer will be reset to 0.
  - At the center of the timer a graphic animation (animated gif)
  - Below the main timer, another digital timer will display the actual elapsed time of the whole session.
  - Segmentation:
    - A meditation Session is divided in segments (1 to 4)
    - Each segment has it's own duration
    - Before the beginning of the first session (end of Preparation time), at the end of the last segment, and between each segment, the Segmentation Sound will be played the number of times indicated in the Segmentation Sound Repetition field of the Session Timer Data
    - Once all the segments are exausted there will be no activity in the main timer. But
    - During the whole duration of the session (or for the number of times indicated in the Meditation Sound Repetition field of the Timer Data) the Meditation Sound will be reproduced.
  - UI:
    - There is a Green Start Button on the bottom of the page. By clicking the button the session will start (State= in_session). The Button will switch to a red Pause button. Clicking the Pause Button the session will pause (State = paused): both the timers will be paused. This happens also when the application becomes inactive (eg: the phone switchs control to another application)
    - Clicking the Pause Button the Button will turn Green again and show the word "Continue" that will resume the Session and resume the timers.
    - On the top left corner of the window there is a back button (`<-`). By clicking that button the window will be closed. If the meditation State == in_session or pause, a pop up will ask if the user want to terminate the session.
    - The session terminates only when the back button is pressed.
    - When the last segment of the meditation is over, the meditation is still ongoing. While at this point the main round timer will be inactive, the digital timer is still running and can be paused only by the pause button. The session terminates only when the user presses the back button.

### 2.4 Session Timer Create/Edit Screen
  - This screen lets the user create a new Session Timer or edit an existing one. It’s a dynamic form, split into sections, with live validation and visual feedback.
    - If creating a new Session Timer:
      - Fields are blank/defaulted.
      - Title bar says: “Create Session Timer”
    - If editing an existing one:
      - All fields pre-populated.
      - Title bar says: “Edit Session Timer”
      - Save button overwrites the existing entry.
  - Layout Sections and Elements
    - 1. 🏷️ Session Info
      - Name Input Field (required)
      - Preparation Time Picker (0 to N minutes)
    - 2. 🔊 Segmentation Sound Section
      - Sound Selector Button
        Opens system ringtone picker (filtered for notifications/alarms)
      - Repetition Count Selector (Dropdown or Stepper: 1–3)
      - Volume Slider (0–5)
    - 3. Meditation Sound Section
      - Sound Source Picker
        Radio buttons or dropdown: `System Sound`, `Local File`
      - Depending on selection:
        - If System Sound → open ringtone picker
        - If Local File → open file picker dialog
      - Repetition Selector:
        - Radio buttons: `Forever` / `Select Count`
        - If `Select Count`: show a Stepper input
    - Volume Slider (0–5)
    - 4. Segmentation Editing Area
      - At the top of this area there will be an "Add Segment" Button
      - Below there will be an area where segments can be added ikn the form of a box
      - Each segment box will have a control to enter duration
      - The first box is automatically added and cannot be removed. All the other boxes will show a 'x' red button on the top right for removal
      - The user is allowed to create Only up to 4 segments. Once the max number of segments is reached the "Add Segment" Button will be disabled. Will be enabled again if one segment is removed.
    - 5. Reminders & Diary
      - Toggle: Daily Reminder On/Off
        - If On, show:
          - Time Picker (what time to remind)
    - 6. Top/Bottom Controls
      - Top Left: `<- Back` — prompts confirmation if unsaved changes
      - Top Right: 🗑️ Delete (only visible in Edit mode)
      - Bottom:
        - “Cancel” button — discards changes and exits
        - “Save” button — only enabled if all required fields are valid
    - 7. Live Behavior Notes
      - Changing sound source clears previous selection
      - If the user tries to exit with unsaved changes, show popup:
      > “You have unsaved changes. Discard or Save?”
      - Fields are validated on-the-fly (e.g. name not empty, segments parseable)

### 2.5 Statistics Page
  The Statistics Page provides the user with a clear and insightful summary of their meditation activity over time.
  - Accessible from the Settings Menu*- via the "View Statistics" entry.
  - Data is derived from the Meditations Log
  - Layout Sections and Functional Requirements

  - 1. Summary Card (Top Section)
  - Total Sessions
    - Displays the total number of meditation sessions logged.
  - Total Time
    - Cumulative meditation time across all sessions.
  - Average Session Duration
    - Computed as (Total Time / Total Sessions), shown in minutes.

  - 2. Statistics Chart
    The chart visualizes how often and how long the user meditated over time.
    It helps identify habits, trends, and gaps in practice.

    Selected Range | Chart X-Axis Unit | Grouped Data per… | Typical Bars/Points
    ---------------|-------------------|-------------------|-------------------
    Last 1 Month | Day | Day | 30 bars
    Last 3 Months | Week | 7-day group | ~12 bars
    Last 6 Months | Week | 7-day group | ~26 bars
    Last 1 Year | Month | Calendar Month | 12 bars
    Last 5 Years | Quarter | 3-month block | 20 bars
    All Time | Year | Calendar Year | Varies (user span)

    -UI
        - On the top of the section a Time period selector: 1M | 3M | 6M | 1Y | 5Y | 10Y | ALL
        - Underneath the chart, a legend explaining the chart data:
          - X-axis: Time Period

-On the bottom a button for sharing the stats

### 2.6 Diary View
  A page That allows to browse all the Diary entries. All the entries displayed as a scrollable vertical list of items in order of timestamp. Each entry has a button to edit the view (through the Diary Entry editor)

## 3. Settings Menu
The Settings Menu is accessible via the settings icon located in the top-right corner of the Main Screen. Upon activation, the menu slides in from the left side of the screen. It allows users to configure global application preferences unrelated to individual Session Timers.

### 3.1. Visual Personalization
- Change Background
  - Allows the user to modify the background Theme of the application based on a finite number of colors. Every Base color will be the base for the application palette (borders, backgrounds, foregrounds).
- Custom Image: Select an image for the Meditation Session Window from the device’s local storage or pick one of the built in images

### 3.2. Session Behavior
- Enable Silent Mode
  - When enabled, the app activates Do Not Disturb mode (flasg in sql) for the duration of an active meditation session (the flag for dnd is in the database)
  - Automatically restores previous system sound state upon session termination.
- Keep Screen On
  - When enabled, prevents the device from sleeping or dimming the screen during a meditation session.9 (flag in sql)
- Count Up Instead of Down (flag in sql)
  - Toggle that controls the direction of the main round session timer.
    - Enabled: Timer displays elapsed time from 0 upwards.
    - Disabled (default): Timer displays countdown for each segment.
### 3.3. Analytics and Progress Tracking
- View Statistics
  - Displays analytical information based on meditation logs.
  - Includes:
    - Total time spent meditating.
    - Number of meditation sessions performed.
    - Weekly and monthly breakdowns via charted views.
### 3.4. Statistics and Diary Sharing via `react-native-share`
- There is a button to share Statistics and one for sharing Diary Entries.
  * Allows users to export meditation stats and diary entries via yaml.
    - Diary Entries are exported in the following format:
        ```yaml
        diary_entries:
        - timestamp: 2025-06-10T07:30:00
            content: >
            Had a really deep session today. Breathing felt effortless.
            Noticed more space between thoughts.

        - timestamp: 2025-06-11T08:15:00
            content: >
            Mind was scattered. Couldn't settle. Felt restless.
        ```
    - Stats are exported using the following format:
        ```yaml
        summary:
        total_sessions: 86
        total_time_minutes: 2850
        average_session_duration_minutes: 33.1

        by_month:
        - month: 2025-05
            total_sessions: 40
            total_minutes: 1280

        - month: 2025-06
            total_sessions: 12
            total_minutes: 420
        ```
### 3.5. **Local Backup/Restore**:
    Daily automated backup of SQLite DB in secure local path must be autorized by the user. In the menu we have a slide to authorize the backup and restore the content of the .db file.
### 3.6. Application Actions
    - Share Button
    - Opens the system share dialog to allow users to share the app link or a custom message via supported apps.
    - Rate Us Button
    - Redirects the user to the app’s Play Store page for rating and feedback submission.
    - Remove Ads (In-App Purchase)
    - Opens an in-app purchase flow to disable advertising permanently.
    - Upon successful purchase, ads are removed across all screens.
    - Buy me a coffee Button

## 4. Monetization
- An Ad banner must be maintained at the bottom of the screen at all the times (minimal and non-disruptive). Must be hiidden if the adsFreePurchased flag in sql is enabled
- In-app purchase to remove ads
- Button with donation/support feature (Buy me a coffee)

## 5. Daily automated tasks:
* Backup creation (every day we backup the SQLite DB file to a secure app-specific local storage folder with timestamp naming)
    - We keep only the last 5 backups, deleting the oldest one when a new backup is created.
* Stats aggregation to ease chart creation (total time, per day/week metrics)
