// =============================================================================
// Courses, Teachable/Thinkific style.
//   #/                      My courses: one card per course with your progress
//   #/course/<topicId>      the curriculum: lessons numbered 1, 2, 3... with ✓ when practised
// A course is a topic; lessons are in the order the API gives (educator position, then publish date).
// "Complete" = practised with ClipSensei at least once - recall, not just watching, is the point.
// =============================================================================
import { el, formatDuration, sensei } from "./dom.js";
import { api } from "./api.js";

const enc = encodeURIComponent;

// Everything the course pages need, fetched together
export async function loadCourses() {
  const [{ rows }, { practised }] = await Promise.all([api("/catalogue"), api("/learn/progress")]);
  return rows.map((course) => {
    const lessons = course.lessons.map((l) => ({ ...l, done: Boolean(practised[l.videoId]) }));
    const doneCount = lessons.filter((l) => l.done).length;
    return { ...course, lessons, doneCount, next: lessons.find((l) => !l.done) || lessons[0] };
  });
}

export const watchHref = (videoId) => `#/watch/${enc(videoId)}`;

export function progressBar(done, total) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  return el("div", { className: "progress" },
    el("div", { className: "bar", role: "progressbar", ariaValueNow: String(percent), ariaValueMin: "0", ariaValueMax: "100" },
      el("span", { style: `width: ${percent}%` })),
    el("small", {}, `${done} of ${total} lesson${total === 1 ? "" : "s"} complete`),
  );
}

function courseAction(course) {
  if (course.doneCount === 0) return "Start course";
  if (course.doneCount === course.lessonCount) return "Review course";
  return "Continue";
}

function lessonMeta(course) {
  const minutes = formatDuration(course.totalDurationMs);
  return `${course.lessonCount} lesson${course.lessonCount === 1 ? "" : "s"}${minutes ? ` · ${minutes}` : ""}`;
}

// ---------------------------------------------------------------------------
// Home: My courses
// ---------------------------------------------------------------------------
export async function renderHome(app, reviewsBanner) {
  const [courses, { reviews }] = await Promise.all([loadCourses(), api("/learn/reviews/due")]);
  const greeting = reviews.length
    ? `Welcome back. ${reviews.length} idea${reviews.length === 1 ? " is" : "s are"} ready for review.`
    : courses.some((c) => c.doneCount) ? "Pick up where you left off." : "Choose a course and start with lesson 1.";

  app.replaceChildren(
    el("section", { className: "courses" },
      el("div", { className: "page-head" },
        el("div", {}, el("p", { className: "eyebrow" }, "Learner"), el("h1", {}, "My courses")),
        sensei(greeting),
      ),
      reviewsBanner(reviews) || "",
      courses.length
        ? el("div", { className: "course-grid" }, ...courses.map((course) =>
            el("a", { className: "course-card", href: `#/course/${enc(course.topicId)}` },
              course.lessons[0].thumbnailUrl ? el("img", { src: course.lessons[0].thumbnailUrl, alt: "" }) : el("div", { className: "thumb-missing" }),
              el("div", { className: "course-body" },
                el("h2", {}, course.topicName),
                el("p", { className: "meta" }, lessonMeta(course)),
                progressBar(course.doneCount, course.lessonCount),
                el("span", { className: "btn" }, courseAction(course)),
              ),
            )))
        : el("p", { className: "meta" }, "No courses published yet."),
    ),
  );
}

// ---------------------------------------------------------------------------
// Course page: the curriculum
// ---------------------------------------------------------------------------
export function curriculum(course, currentId) {
  return el("ol", { className: "curriculum" }, ...course.lessons.map((lesson) =>
    el("li", {},
      el("a", {
        className: `lesson-row ${lesson.done ? "done" : ""} ${lesson.videoId === currentId ? "current" : ""}`,
        href: watchHref(lesson.videoId), ariaCurrent: lesson.videoId === currentId ? "page" : null,
      },
        el("span", { className: "num", ariaHidden: "true" }, lesson.done ? "✓" : String(lesson.lessonNumber)),
        el("span", { className: "lesson-title" }, lesson.title),
        el("small", {}, [formatDuration(lesson.durationMs), lesson.done ? "practised" : ""].filter(Boolean).join(" · ")),
      ),
    )));
}

export async function renderCourse(app, topicId) {
  const course = (await loadCourses()).find((c) => c.topicId === topicId);
  if (!course) throw new Error("Course not found.");
  app.replaceChildren(
    el("section", { className: "courses" },
      el("a", { className: "back", href: "#/" }, "← My courses"),
      el("div", { className: "course-hero" },
        el("div", {},
          el("p", { className: "eyebrow" }, "Course"),
          el("h1", {}, course.topicName),
          el("p", { className: "meta" }, lessonMeta(course)),
          progressBar(course.doneCount, course.lessonCount),
          el("a", { className: "btn big", href: watchHref(course.next.videoId) },
            `${courseAction(course)}: Lesson ${course.next.lessonNumber}`),
        ),
        course.lessons[0].thumbnailUrl ? el("img", { src: course.lessons[0].thumbnailUrl, alt: "" }) : null,
      ),
      el("h2", {}, "Course content"),
      curriculum(course),
    ),
  );
}
